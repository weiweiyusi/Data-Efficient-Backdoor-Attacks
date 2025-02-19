import tqdm, torch, os
import numpy as np
from torch.utils.data import DataLoader
import torch.optim as optim
import torch.nn as nn

from opts import get_opts
from utils.utils import get_name
from utils.settings import DATASETTINGS
from models import build_model
from datasets import build_transform, build_data
from attacks import build_trigger


def transfer(opts):
    name = get_name(opts, 'transfer')
    print('transfer', name)
    DSET = DATASETTINGS[opts.data_name]
    train_transform = build_transform(True, DSET['img_size'], DSET['crop'], DSET['flip'])
    val_transform = build_transform(False, DSET['img_size'], DSET['crop'], DSET['flip'])
    trigger = build_trigger(opts.attack_name, DSET['img_size'], DSET['num_data'], mode=0, target=opts.target, trigger=opts.trigger)
    train_data = build_data(opts.data_name, opts.data_path, True, trigger, train_transform)
    val_data = build_data(opts.data_name, opts.data_path, False, trigger, val_transform)
    samples_idx = np.load(os.path.join(opts.sample_path, '{}.npy'.format(opts.samples_idx)))  # read poison samples idx
    print('poisoned samples len: ', len(samples_idx))
    train_data.data = np.concatenate((train_data.data, train_data.data[samples_idx]), axis=0)  # append selected poisoned samples to the clean train dataset
    train_data.targets = train_data.targets + [train_data.targets[i] for i in samples_idx]
    train_loader = DataLoader(dataset=train_data, batch_size=256, shuffle=True, num_workers=2)
    val_loader = DataLoader(dataset=val_data, batch_size=256, shuffle=False, num_workers=2)

    model = build_model(opts.model_name, DSET['num_classes']).to(opts.device)
    optimizer = optim.SGD(model.parameters(), lr=0.01, weight_decay=5e-4, momentum=0.9)  # or use other hyperparameters
    scheduler = optim.lr_scheduler.MultiStepLR(optimizer, [35, 55], 0.1)
    criterion = nn.CrossEntropyLoss().to(opts.device)

    # 初始化存储容器
    transfer_metrics = {
        'epoch': [],
        'train_acc': [],
        'val_acc': [],
        'back_acc': []
    }

    for epoch in range(70):
        trigger.set_mode(0), model.train()
        correct, total, ps, ds = 0, 0, [], []
        desc = 'train - epoch: {:3d}, acc: {:.3f}'
        run_tqdm = tqdm.tqdm(train_loader, desc=desc.format(epoch, 0, 0, 0), disable=opts.disable)
        for x, y, b, s, d in run_tqdm:
            x, y, b, s, d = x.to(opts.device), y.to(opts.device), b.to(opts.device), s.to(opts.device), d.to(opts.device)
            optimizer.zero_grad()
            p = model(x)
            loss_cls = criterion(p, y)
            loss_cls.backward()
            _, p = torch.max(p, dim=1)
            correct += (p == y).sum().item()
            total += y.shape[0]
            optimizer.step()
            run_tqdm.set_description(desc.format(epoch, correct / (total + 1e-12)))
        scheduler.step()
        train_acc = correct / (total + 1e-8)

        trigger.set_mode(1), model.eval()
        correct, total = 0, 0
        desc = 'val   - epoch: {:3d}, acc: {:.3f}'
        run_tqdm = tqdm.tqdm(val_loader, desc=desc.format(0, 0), disable=opts.disable)
        for x, y, _, _, _ in run_tqdm:
            x, y = x.to(opts.device), y.to(opts.device)
            with torch.no_grad():
                p = model(x)
            _, p = torch.max(p, dim=1)
            correct += (p == y).sum().item()
            total += y.shape[0]
            run_tqdm.set_description(desc.format(epoch, correct / total))
        val_acc = correct / (total + 1e-8)

        trigger.set_mode(2), model.eval()
        correct, total = 0, 0
        desc = 'back  - epoch: {:3d}, acc: {:.3f}'
        run_tqdm = tqdm.tqdm(val_loader, desc=desc.format(0, 0), disable=opts.disable)
        for x, y, b, _, _ in run_tqdm:
            x, y, b = x.to(opts.device), y.to(opts.device), b.to(opts.device)
            idx = b == 1
            x, y, b = x[idx, :, :, :], y[idx], b[idx]
            if x.shape[0] == 0: continue
            with torch.no_grad():
                p = model(x)
            _, p = torch.max(p, dim=1)
            correct += (p == y).sum().item()
            total += y.shape[0]
            run_tqdm.set_description(desc.format(epoch, correct / total))
        back_acc = correct / (total + 1e-8)

        if opts.disable:
            print('epoch: {:3d}, train_acc: {:.3f}, val_acc: {:.3f}, back_acc: {:.3f}'.format(epoch, train_acc, val_acc, back_acc))

        # 记录指标
        transfer_metrics['epoch'].append(epoch)
        transfer_metrics['train_acc'].append(train_acc)
        transfer_metrics['val_acc'].append(val_acc)
        transfer_metrics['back_acc'].append(back_acc)

    # 保存数据到文件
    pd.DataFrame(transfer_metrics).to_csv(os.path.join(opts.log_path, f'{name}_transfer_metrics.csv'), index=False)

if __name__ == '__main__':
    opts = get_opts()
    transfer(opts)
