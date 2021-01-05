import sys
sys.path.append("..")
import torch
import torch.nn as nn
import torch.nn.functional as F
from util_wqaq import QuantConv2d, QuantBNFuseConv2d, QuantReLU, QuantMaxPool2d, QuantAvgPool2d

def channel_shuffle(x, groups):
    """shuffle channels of a 4-D Tensor"""
    batch_size, channels, height, width = x.size()
    assert channels % groups == 0
    channels_per_group = channels // groups
    # split into groups
    x = x.view(batch_size, groups, channels_per_group, height, width)
    # transpose 1, 2 axis
    x = x.transpose(1, 2).contiguous()
    # reshape into orignal
    x = x.view(batch_size, channels, height, width)
    return x

class QuantConvBNReLU(nn.Module):
    def __init__(self, input_channels, output_channels,
                 kernel_size=-1, stride=-1, padding=-1, groups=1, channel_shuffle=0, shuffle_groups=1, abits=8, wbits=8, bn_fuse=0, q_type=1, q_level=0, first_layer=0):
        super(QuantConvBNReLU, self).__init__()
        self.channel_shuffle_flag = channel_shuffle
        self.shuffle_groups = shuffle_groups
        self.bn_fuse = bn_fuse

        if self.bn_fuse == 1:
            self.quant_bn_fuse_conv = QuantBNFuseConv2d(input_channels, output_channels,
                    kernel_size=kernel_size, stride=stride, padding=padding, groups=groups, a_bits=abits, w_bits=wbits, q_type=q_type, q_level=q_level, first_layer=first_layer)
        else:
            self.quant_conv = QuantConv2d(input_channels, output_channels,
                    kernel_size=kernel_size, stride=stride, padding=padding, groups=groups, a_bits=abits, w_bits=wbits, q_type=q_type, q_level=q_level, first_layer=first_layer)
            self.bn = nn.BatchNorm2d(output_channels, momentum=0.01) # 考虑量化带来的抖动影响,对momentum进行调整(0.1 ——> 0.01),削弱batch统计参数占比，一定程度抑制抖动。经实验量化训练效果更好,acc提升1%左右
        self.relu = QuantReLU(inplace=True, a_bits=abits, q_type=q_type)

    def forward(self, x):
        if self.channel_shuffle_flag:
            x = channel_shuffle(x, groups=self.shuffle_groups)
        if self.bn_fuse == 1:
            x = self.quant_bn_fuse_conv(x)
        else:
            x = self.quant_conv(x)
            x = self.bn(x)
        x = self.relu(x)
        return x

class Net(nn.Module):
    def __init__(self, cfg = None, abits=8, wbits=8, bn_fuse=0, q_type=1, q_level=0):
        super(Net, self).__init__()
        if cfg is None:
            cfg = [256, 256, 256, 512, 512, 512, 1024, 1024]
        # model - A/W全量化(除输入、输出外)
        self.quant_model = nn.Sequential(
                QuantConvBNReLU(3, cfg[0], kernel_size=5, stride=1, padding=2, abits=abits, wbits=wbits, bn_fuse=bn_fuse, q_type=q_type, q_level=q_level, first_layer=1),
                QuantConvBNReLU(cfg[0], cfg[1], kernel_size=1, stride=1, padding=0, groups=2, channel_shuffle=0, abits=abits, wbits=wbits, bn_fuse=bn_fuse, q_type=q_type, q_level=q_level),
                QuantConvBNReLU(cfg[1], cfg[2], kernel_size=1, stride=1, padding=0, groups=2, channel_shuffle=1, shuffle_groups=2, abits=abits, wbits=wbits, bn_fuse=bn_fuse, q_type=q_type, q_level=q_level),
                QuantMaxPool2d(kernel_size=2, stride=2, padding=0, a_bits=abits, q_type=q_type),
                
                QuantConvBNReLU(cfg[2], cfg[3], kernel_size=3, stride=1, padding=1, groups=16, channel_shuffle=1, shuffle_groups=2, abits=abits, wbits=wbits, bn_fuse=bn_fuse, q_type=q_type, q_level=q_level),
                QuantConvBNReLU(cfg[3], cfg[4], kernel_size=1, stride=1, padding=0, groups=4, channel_shuffle=1, shuffle_groups=16, abits=abits, wbits=wbits, bn_fuse=bn_fuse, q_type=q_type, q_level=q_level),
                QuantConvBNReLU(cfg[4], cfg[5], kernel_size=1, stride=1, padding=0, groups=4, channel_shuffle=1, shuffle_groups=4, abits=abits, wbits=wbits, bn_fuse=bn_fuse, q_type=q_type, q_level=q_level),
                QuantMaxPool2d(kernel_size=2, stride=2, padding=0, a_bits=abits, q_type=q_type),
                
                QuantConvBNReLU(cfg[5], cfg[6], kernel_size=3, stride=1, padding=1, groups=32, channel_shuffle=1, shuffle_groups=4, abits=abits, wbits=wbits, bn_fuse=bn_fuse, q_type=q_type, q_level=q_level),
                QuantConvBNReLU(cfg[6], cfg[7], kernel_size=1, stride=1, padding=0, groups=8, channel_shuffle=1, shuffle_groups=32, abits=abits, wbits=wbits, bn_fuse=bn_fuse, q_type=q_type, q_level=q_level),
                QuantConvBNReLU(cfg[7], 10, kernel_size=1, stride=1, padding=0, abits=abits, wbits=wbits, bn_fuse=bn_fuse, q_type=q_type, q_level=q_level),
                QuantAvgPool2d(kernel_size=8, stride=1, padding=0, a_bits=abits, q_type=q_type)
                )

    def forward(self, x):
        x = self.quant_model(x)
        x = x.view(x.size(0), -1)
        return x
        