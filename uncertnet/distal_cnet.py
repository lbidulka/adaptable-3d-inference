from typing import Any
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from tqdm import tqdm
import copy

from types import SimpleNamespace
    
class distal_err_net(torch.nn.Module):
    '''
    For a single limb correction
    '''
    def __init__(self, in_kpts=[7, 11, 12, 13], hidden_dim=32):
        super().__init__()

        in_dim = len(in_kpts) * 3

        self.dr1 = nn.Dropout(0.2)
        self.l1 = nn.Linear(in_dim, hidden_dim)  
        self.bn1 = nn.BatchNorm1d(hidden_dim)
        self.l2 = nn.Linear(hidden_dim, hidden_dim)  
        self.bn2 = nn.BatchNorm1d(hidden_dim)
        self.l3 = nn.Linear(hidden_dim, hidden_dim)  
        self.bn3 = nn.BatchNorm1d(hidden_dim)
        self.out = nn.Linear(hidden_dim, 3)

    def forward(self, x):
        x = self.dr1(torch.nn.functional.leaky_relu(self.bn1(self.l1(x))))
        x = self.dr1(torch.nn.functional.leaky_relu(self.bn2(self.l2(x))))
        x = self.dr1(torch.nn.functional.leaky_relu(self.bn3(self.l3(x))))
        return self.out(x)

class distal_cnet():
    '''
    single distal keypoint correcter

    in_kpts: list of indices of the input keypoints, where the last is the distal joint

    Assume all data is of dims: (batch_size, num_kpts, 3)
    '''
    def __init__(self, in_kpts=[7, 11, 12, 13], hidden_dim=32) -> None:
        self._setup_config()
        self.in_kpts = in_kpts
        self.net = distal_err_net(in_kpts=in_kpts, hidden_dim=hidden_dim).to(self.config.device)

        self.optimizer = torch.optim.Adam(self.net.parameters(), lr=self.config.lr)
        self.criterion = torch.nn.MSELoss()
    
    def _setup_config(self):
        config = SimpleNamespace()
        # Misc
        config.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        config.joint_names = {
            'Pelvis': 0,
            'L_Hip': 1, 
            'L_Knee': 2, 
            'L_Ankle': 3,
            'R_Hip': 4, 
            'R_Knee': 5, 
            'R_Ankle': 6, 
            'Torso': 7, 
            'Neck': 8, 
            'Nose': 9, 
            'Head': 10, 
            'L_Shoulder': 11, 
            'L_Elbow': 12, 
            'L_Wrist': 13, 
            'R_Shoulder': 14, 
            'R_Elbow': 15, 
            'R_Wrist': 16,
        }
        # Training
        config.err_scale = 1000
        config.lr = 5e-3

        self.config = config
    
    def get_data(self, data, targets=None):
        data_kpts = data[:, self.in_kpts, :]
        return (data_kpts, targets[:, self.in_kpts[-1], :]) if targets is not None else data_kpts
    
    def train_step(self, data, targets):
        data_kpts, targets_kpts = self.get_data(data, targets)

        preds = self.net(torch.flatten(data_kpts, 1))
        loss = self.loss(preds, torch.flatten(targets_kpts, 1)) 
        loss.backward()
        self.optimizer.step()
        self.optimizer.zero_grad()

        return loss.item()
    
    def val_step(self, data, targets):
        with torch.no_grad():
            data_kpts, targets_kpts = self.get_data(data, targets)

            preds = self.net(torch.flatten(data_kpts, 1))
            loss = self.loss(preds, torch.flatten(targets_kpts, 1)) 
        return loss.item()
    
    def loss(self, preds, targets):
        return self.criterion(preds, targets*self.config.err_scale) 
    
    def __call__(self, data):
        data_kpts = self.get_data(data)
        pred_err = self.net(torch.flatten(data_kpts, 1)) / self.config.err_scale        
        data[:, self.in_kpts[-1], :] -= pred_err
        return data
    
    def save(self, path):
        torch.save(self.net.state_dict(), path)

    

class multi_distal_cnet():
    '''
    multi-limb correction for 3D pose
    '''
    def __init__(self, config) -> None:
        self.config = config
        # identity for when not using a limb
        self.net_LArm = lambda x: x
        self.net_RArm = lambda x: x
        self.net_LLeg = lambda x: x
        self.net_RLeg = lambda x: x

        cnet_hidden_dim = 64
        config.cnet_train_epochs = 10

        self.net_LArm = distal_cnet(in_kpts=[8, 11, 12, 13], hidden_dim=cnet_hidden_dim)  # Neck, L_Shoulder, L_Elbow, L_Wrist
        self.net_RArm = distal_cnet(in_kpts=[8, 14, 15, 16], hidden_dim=cnet_hidden_dim)  # Neck, R_Shoulder, R_Elbow, R_Wrist
        self.net_LLeg = distal_cnet(in_kpts=[0, 1, 2, 3], hidden_dim=cnet_hidden_dim)  # Pelvis, L_Hip, L_Knee, L_Ankle
        self.net_RLeg = distal_cnet(in_kpts=[0, 4, 5, 6], hidden_dim=cnet_hidden_dim)  # Pelvis, R_Hip, R_Knee, R_Ankle


        # cnet_LArm = uncertnet_models.distal_cnet(in_kpts=[0, 7, 8, 11, 12, 13])  # Pelvis, Torso, Neck, L_Shoulder, L_Elbow, L_Wrist
        # cnet_RArm = uncertnet_models.distal_cnet(in_kpts=[0, 7, 8, 14, 15, 16])  # Pelvis, Torso, Neck, R_Shoulder, R_Elbow, R_Wrist

        # cnet_LArm = uncertnet_models.distal_cnet(in_kpts=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15, 16, 13])
        # cnet_RArm = uncertnet_models.distal_cnet(in_kpts=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16])

        # cnet_LArm = uncertnet_models.distal_cnet(in_kpts=[10, 11, 12])  # why is this one so good?
        # cnet_RArm = uncertnet_models.distal_cnet(in_kpts=[10, 14, 15])

        # cnet_LArm = uncertnet_models.distal_cnet(in_kpts=[11, 12, 13])  # L_Shoulder, L_Elbow, L_Wrist
        # cnet_RArm = uncertnet_models.distal_cnet(in_kpts=[14, 15, 16])  # R_Shoulder, R_Elbow, R_Wrist

        # cnet_LArm = uncertnet_models.distal_cnet(in_kpts=[7, 11, 12, 13])  # Torso, L_Shoulder, L_Elbow, L_Wrist
        # cnet_RArm = uncertnet_models.distal_cnet(in_kpts=[7, 14, 15, 16])  # Torso, R_Shoulder, R_Elbow, R_Wrist
    
    def eval(self):
        if hasattr(self.net_RArm, 'eval'):
            self.net_RArm.net.eval()
        if hasattr(self.net_LArm, 'eval'):
            self.net_LArm.net.eval()
        if hasattr(self.net_RLeg, 'eval'):
            self.net_RLeg.net.eval()
        if hasattr(self.net_LLeg, 'eval'):
            self.net_LLeg.net.eval()

    def __call__(self, pred) -> Any:
        corr_pred = copy.deepcopy(pred)
        corr_pred = self.net_LArm(corr_pred)
        corr_pred = self.net_RArm(corr_pred)
        corr_pred = self.net_LLeg(corr_pred)
        corr_pred = self.net_RLeg(corr_pred)
        return corr_pred
    
    def load_cnets(self,):
        '''
        Loads best validation checkpoint for each cnet
        '''
        if hasattr(self.net_LArm, 'load_cnet'):
            cnet_LArm_ckpt_dict = torch.load(self.config.cnet_ckpt_path + 'cnet_LArm.pth')
            self.net_LArm.net.load_state_dict(cnet_LArm_ckpt_dict)  
        if hasattr(self.net_RArm, 'load_cnet'):
            cnet_RArm_ckpt_dict = torch.load(self.config.cnet_ckpt_path + 'cnet_RArm.pth')
            self.net_RArm.net.load_state_dict(cnet_RArm_ckpt_dict)
        if hasattr(self.net_LLeg, 'load_cnet'):
            cnet_LLeg_ckpt_dict = torch.load(self.config.cnet_ckpt_path + 'cnet_LLeg.pth')
            self.net_LLeg.net.load_state_dict(cnet_LLeg_ckpt_dict)
        if hasattr(self.net_RLeg, 'load_cnet'):
            cnet_RLeg_ckpt_dict = torch.load(self.config.cnet_ckpt_path + 'cnet_RLeg.pth')
            self.net_RLeg.net.load_state_dict(cnet_RLeg_ckpt_dict)
    
    def train(self,):
        '''
        Train the cnet models, then reload the best validation checkpoints
        '''
        if hasattr(self.net_LArm, 'train_step'):
            print("\nTraining CNET_LARM")
            self.train_cnet(self.net_LArm, self.config.cnet_ckpt_path + 'cnet_LArm.pth')
            # cnet_LArm_ckpt_dict = torch.load(self.config.cnet_ckpt_path + 'cnet_LArm.pth')
            # self.net_LArm.net.load_state_dict(cnet_LArm_ckpt_dict)  
        if hasattr(self.net_RArm, 'train_step'):
            print("\nTraining CNET_RARM")
            self.train_cnet(self.net_RArm, self.config.cnet_ckpt_path + 'cnet_RArm.pth')
            # cnet_RArm_ckpt_dict = torch.load(self.config.cnet_ckpt_path + 'cnet_RArm.pth')
            # self.net_RArm.net.load_state_dict(cnet_RArm_ckpt_dict)
        if hasattr(self.net_LLeg, 'train_step'):
            print("\nTraining CNET_LLEG")
            self.train_cnet(self.net_LLeg, self.config.cnet_ckpt_path + 'cnet_LLeg.pth')
            # cnet_LLeg_ckpt_dict = torch.load(self.config.cnet_ckpt_path + 'cnet_LLeg.pth')
            # self.net_LLeg.net.load_state_dict(cnet_LLeg_ckpt_dict)
        if hasattr(self.net_RLeg, 'train_step'):
            print("\nTraining CNET_RLEG")
            self.train_cnet(self.net_RLeg, self.config.cnet_ckpt_path + 'cnet_RLeg.pth')
            # cnet_RLeg_ckpt_dict = torch.load(self.config.cnet_ckpt_path + 'cnet_RLeg.pth')
            # self.net_RLeg.net.load_state_dict(cnet_RLeg_ckpt_dict)
        
        self.load_cnets()

    def train_cnet(self, cnet, save_path,):
        ''''
        Train a single distal cnet
        '''
        data_path = self.config.cnet_dataset_path + 'cnet_hybrik_train.npy'
        if self.config.train_datalim is not None:
            data_all = torch.from_numpy(np.load(data_path)).float()[:, :self.config.train_datalim]
        else:
            data_all = torch.from_numpy(np.load(data_path)).float()

        len_train = int(len(data_all[0]) * 0.7)
        data_train, data_val = data_all[:, :len_train, :], data_all[:, len_train:, :]

        data_train = data_train.permute(1,0,2)
        data_val = data_val.permute(1,0,2)
        data_train = data_train.reshape(data_train.shape[0], 2, -1, 3) # batch, 2, kpts, xyz)
        data_val = data_val.reshape(data_val.shape[0], 2, -1, 3)

        # Data/Setup
        batch_size = 1024
        gt_trainset = torch.utils.data.TensorDataset(data_train)
        gt_trainloader = torch.utils.data.DataLoader(gt_trainset, batch_size=batch_size, shuffle=True, num_workers=16, drop_last=False)
        gt_valset = torch.utils.data.TensorDataset(data_val)
        gt_valloader = torch.utils.data.DataLoader(gt_valset, batch_size=batch_size, shuffle=True, num_workers=16, drop_last=False)

        eps = self.config.cnet_train_epochs
        b_print_freq = 150
        best_val_loss = 1e10

        for ep in tqdm(range(eps), dynamic_ncols=True):
            cnet_train_losses = []
            for batch_idx, data in enumerate(gt_trainloader):
                backbone_pred = data[0][:, 0, :].to('cuda')
                target_xyz_17 = data[0][:, 1, :].to('cuda')

                cnet_target = backbone_pred - target_xyz_17
                cnet_train_losses.append(cnet.train_step(backbone_pred, cnet_target))

                if (batch_idx != 0) and (batch_idx % b_print_freq == 0):
                        print(f" - B {batch_idx} loss: {np.mean(cnet_train_losses):.5f}")
            
            with torch.no_grad():
                cnet_val_losses = []
                for batch_idx, data in enumerate(gt_valloader):
                    backbone_pred = data[0][:, 0, :].to('cuda')
                    target_xyz_17 = data[0][:, 1, :].to('cuda')

                    cnet_target = backbone_pred - target_xyz_17
                    cnet_val_losses.append(cnet.val_step(backbone_pred, cnet_target))

                mean_val_loss = np.mean(cnet_val_losses)
                print(f"EP {ep}:    t_loss: {np.mean(cnet_train_losses):.5f}    v_loss: {mean_val_loss:.5f}")
                
                if mean_val_loss < best_val_loss:
                    print(" ---> best val loss so far, saving model...")
                    cnet.save(save_path)
                    best_val_loss = mean_val_loss
        return
