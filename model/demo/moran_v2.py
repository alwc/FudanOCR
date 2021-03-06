# -*- coding: utf-8 -*-

def demo_moran_v2(config_file):
    import sys
    sys.path.append('./recognition_model/MORAN_V2')

    import argparse
    import random
    import torch
    import torch.backends.cudnn as cudnn
    import torch.optim as optim
    import torch.utils.data
    from torch.autograd import Variable
    import numpy as np
    import os
    import MORAN_V2.tools.utils as utils
    import MORAN_V2.tools.dataset as dataset
    import time
    import cv2
    from collections import OrderedDict

    from yacs.config import CfgNode as CN

    def read_config_file(config_file):
        # 用yaml重构配置文件
        f = open(config_file)
        opt = CN.load_cfg(f)
        return opt

    opt = read_config_file(config_file)
    nclass = len(opt.alphabet.split(opt.sep))
    nc = 1

    converter = utils.strLabelConverterForAttention(opt.alphabet, opt.sep)
    criterion = torch.nn.CrossEntropyLoss()

    # 在这里修改超参数的读入
    from MORAN_V2.models.moran import MORAN
    print(opt)

    test_dataset = dataset.lmdbDataset(root=opt.valroot,
                                       transform=dataset.resizeNormalize((opt.imgW, opt.imgH)),
                                       reverse=opt.BidirDecoder)

    if opt.cuda:
        MORAN = MORAN(nc, nclass, opt.nh, opt.targetH, opt.targetW, BidirDecoder=opt.BidirDecoder, CUDA=opt.cuda)
    else:
        MORAN = MORAN(nc, nclass, opt.nh, opt.targetH, opt.targetW, BidirDecoder=opt.BidirDecoder,
                      inputDataType='torch.FloatTensor', CUDA=opt.cuda)

    if opt.MORAN != '':
        print('loading pretrained model from %s' % opt.MORAN)
        if opt.cuda:
            state_dict = torch.load(opt.MORAN)
        else:
            state_dict = torch.load(opt.MORAN, map_location='cpu')
        MORAN_state_dict_rename = OrderedDict()
        for k, v in state_dict.items():
            name = k.replace("module.", "")  # remove `module.`
            MORAN_state_dict_rename[name] = v
        MORAN.load_state_dict(MORAN_state_dict_rename, strict=True)

    image = torch.FloatTensor(opt.batchSize, nc, opt.imgH, opt.imgW)
    text = torch.LongTensor(opt.batchSize * 5)
    text_rev = torch.LongTensor(opt.batchSize * 5)
    length = torch.IntTensor(opt.batchSize)

    if opt.cuda:
        MORAN.cuda()
        MORAN = torch.nn.DataParallel(MORAN, device_ids=range(opt.ngpu))
        image = image.cuda()
        text = text.cuda()
        text_rev = text_rev.cuda()
        criterion = criterion.cuda()

    image = Variable(image)
    text = Variable(text)
    text_rev = Variable(text_rev)
    length = Variable(length)

    def val(dataset, criterion, max_iter=1000):
        print('Start val')
        data_loader = torch.utils.data.DataLoader(
            dataset, shuffle=False, batch_size=opt.batchSize, num_workers=int(opt.workers))  # opt.batchSize
        val_iter = iter(data_loader)
        max_iter = min(max_iter, len(data_loader))
        n_correct = 0
        n_total = 0
        loss_avg = utils.averager()

        # 生成一个临时文件夹，如果已经存在则将其清空
        try:
            import shutil
            shutil.rmtree('./MORAN_DEMO')
            # os.makedirs('./MORAN_DEMO')
        except:
            pass
        os.makedirs('./MORAN_DEMO')
        record_file = open('./MORAN_DEMO/result.txt','a',encoding='utf-8')

        img_cnt = 0
        for i in range(max_iter):
            data = val_iter.next()
            if opt.BidirDecoder:
                cpu_images, cpu_texts, cpu_texts_rev = data
                utils.loadData(image, cpu_images)
                t, l = converter.encode(cpu_texts, scanned=True)
                t_rev, _ = converter.encode(cpu_texts_rev, scanned=True)
                utils.loadData(text, t)
                utils.loadData(text_rev, t_rev)
                utils.loadData(length, l)
                preds0, preds1 = MORAN(image, length, text, text_rev, test=True)
                cost = criterion(torch.cat([preds0, preds1], 0), torch.cat([text, text_rev], 0))
                preds0_prob, preds0 = preds0.max(1)
                preds0 = preds0.view(-1)
                preds0_prob = preds0_prob.view(-1)
                sim_preds0 = converter.decode(preds0.data, length.data)
                preds1_prob, preds1 = preds1.max(1)
                preds1 = preds1.view(-1)
                preds1_prob = preds1_prob.view(-1)
                sim_preds1 = converter.decode(preds1.data, length.data)
                sim_preds = []
                for j in range(cpu_images.size(0)):
                    text_begin = 0 if j == 0 else length.data[:j].sum()
                    if torch.mean(preds0_prob[text_begin:text_begin + len(sim_preds0[j].split('$')[0] + '$')]).data[0] > \
                            torch.mean(
                                preds1_prob[text_begin:text_begin + len(sim_preds1[j].split('$')[0] + '$')]).data[0]:
                        sim_preds.append(sim_preds0[j].split('$')[0] + '$')
                    else:
                        sim_preds.append(sim_preds1[j].split('$')[0][-1::-1] + '$')
            else:
                cpu_images, cpu_texts = data
                utils.loadData(image, cpu_images)
                t, l = converter.encode(cpu_texts, scanned=True)
                utils.loadData(text, t)
                utils.loadData(length, l)
                preds = MORAN(image, length, text, text_rev, test=True)
                cost = criterion(preds, text)
                _, preds = preds.max(1)
                preds = preds.view(-1)
                sim_preds = converter.decode(preds.data, length.data)

            loss_avg.add(cost)
            for img, pred, target in zip(cpu_images, sim_preds, cpu_texts):
                # TODO
                print("图片 ", img, "预测 ", pred, " 目标 ", target)
                # print("图片的尺寸为",img.size())
                img = img.permute(1,2,0)

                cv2.imwrite('./MORAN_DEMO/'+str(img_cnt)+'.jpg', (img.numpy()+1.0)*128)
                record_file.write('./MORAN_DEMO/'+str(img_cnt)+'.jpg' + '  ' + pred + '  ' + target + ' \n')
                img_cnt += 1


                if pred == target.lower():
                    # print("预测 ",pred," 目标 ",target)
                    n_correct += 1
                n_total += 1

        print("correct / total: %d / %d, " % (n_correct, n_total))
        accuracy = n_correct / float(n_total)
        print('Test loss: %f, accuray: %f' % (loss_avg.val(), accuracy))

        record_file.close()
        
        return accuracy

    for p in MORAN.parameters():
        p.requires_grad = False
    MORAN.eval()

    val(test_dataset, criterion)
