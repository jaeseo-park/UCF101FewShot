import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision.models import resnet18, resnet50
from torchvision.models.video import r2plus1d_18
from utils import freeze_all, freeze_layer, freeze_bn, initialize_linear
# torch.backends.cudnn.enabled = False

class R2Plus1D(nn.Module):
    def __init__(self, way=5, shot=1, query=5):
        super(R2Plus1D, self).__init__()
        self.way = way
        self.shot = shot
        self.query = query

        # r2plus1d_18
        model = r2plus1d_18(pretrained=True)
        
        # encoder(freezing)
        self.encoder_freeze = nn.Sequential(
            model.stem,
            model.layer1,
            model.layer2,
            model.layer3,
        )
        self.encoder_freeze.apply(freeze_all)

        # encoder(fine-tuning target)
        self.encoder_tune = nn.Sequential(
            model.layer4,
            nn.AdaptiveAvgPool3d(output_size=(1, 1, 1))
        )

        # scaler
        self.scaler = nn.Parameter(torch.tensor(5.0))

    def forward(self, shot, query):
        x = torch.cat((shot, query), dim=0)
        b, d, c, h, w = x.shape

        # encoder
        x = x.transpose(1, 2).contiguous() # b, c, d, h, w
        x = self.encoder_freeze(x)
        x = self.encoder_tune(x).squeeze()

        shot, query = x[:shot.size(0)], x[shot.size(0):]

        # make prototype
        shot = shot.reshape(self.shot, self.way, -1).mean(dim=0)

        # cosine similarity
        shot = F.normalize(shot, dim=-1)
        query = F.normalize(query, dim=-1)
        logits = torch.mm(query, shot.t())

        return logits * self.scaler

class Resnet(nn.Module):
    def __init__(self, way=5, shot=1, query=5, hidden_size=1024, num_layers=1, bidirectional=True):
        super(Resnet, self).__init__()
        self.way = way
        self.shot = shot
        self.query = query

        # resnet18(freezing)
        model = resnet18(pretrained=True)
        self.encoder_freeze = nn.Sequential(
            model.conv1,
            model.bn1,
            model.relu,
            model.maxpool,
            model.layer1,
            model.layer2,
            model.layer3,
            model.layer4,
            model.avgpool,
        )
        self.encoder_freeze.apply(freeze_all)

        self.last_dim = model.fc.in_features

        # lstm
        self.lstm = nn.LSTM(self.last_dim, hidden_size, num_layers, batch_first=True, bidirectional=bidirectional)

        # linear
        self.linear = nn.Linear(int(hidden_size*2) if bidirectional else hidden_size, hidden_size)
        self.linear.apply(initialize_linear)

        # scaler
        self.scaler = nn.Parameter(torch.tensor(5.0))

    def forward(self, shot, query):
        x = torch.cat((shot, query), dim=0)
        b, d, c, h, w = x.shape

        # encoder
        x = x.view(b * d, c, h, w)
        x = self.encoder_freeze(x)

        # lstm
        x = x.view(b, d, self.last_dim)
        x = (self.lstm(x)[0]).mean(1) # this may be helful for generalization
        # linear
        x = self.linear(x)
        
        shot, query = x[:shot.size(0)], x[shot.size(0):]

        # make prototype
        shot = shot.reshape(self.shot, self.way, -1).mean(dim=0)

        # cosine similarity
        shot = F.normalize(shot, dim=-1)
        query = F.normalize(query, dim=-1)        
        logits = torch.mm(query, shot.t())

        return logits * self.scaler