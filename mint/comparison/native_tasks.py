"""Native forward calls; these adapters never write into external repositories."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from mint.runtime import atomic_npz,atomic_json


class AceRunner:
    def __init__(self,ctx):
        import torch,yaml
        from ace_ego_hand.models.geodit_model import GeoDitModel
        from ace_ego_hand.video_vae import load_vae
        from .native import _ace_size
        self.ctx=ctx;self.device=torch.device('cuda:0')
        self.width,self.height=_ace_size(ctx.sample['width'],ctx.sample['height'],ctx.options['encode_width'])
        self.given=ctx.request['algorithm']=='ace_kgiven'
        root=Path(ctx.spec['root']);suffix='k' if self.given else 'kfree'
        self.model=GeoDitModel(yaml.safe_load((root/f'options/ace_ego_hand_{suffix}.yml').read_text()),self.device)
        self.model.load_inference(str(root/f'checkpoints/ace_ego_hand_{suffix}.pt'))
        self.vae=load_vae(self.device)
        self.frames=np.load(ctx.sample['frames_path'],mmap_mode='r',allow_pickle=False)
        if self.given:
            K=np.array(ctx.sample['camera']['K'],float)
            K[0]*=self.width/ctx.sample['width'];K[1]*=self.height/ctx.sample['height']
            self.intr={'fx':K[0,0],'fy':K[1,1],'cx':K[0,2],'cy':K[1,2],
                'image_width':self.width,'image_height':self.height,'source':ctx.sample['camera']['source']}
        else:
            f=.5*self.width/np.tan(np.deg2rad(30))
            self.intr={'fx':f,'fy':f,'cx':self.width/2,'cy':self.height/2,
                'image_width':self.width,'image_height':self.height,
                'source':'nominal placeholder; K-free geometry uses predicted rays, not this K'}

    def __call__(self,item):
        import torch,cv2
        from ace_ego_hand.video_vae import encode
        from ace_ego_hand.inference import predict_video,DUMP_KEYS
        start,stop=item['start'],item['end'];count=stop-start
        pixels=np.stack([cv2.resize(np.asarray(f),(self.width,self.height),interpolation=cv2.INTER_AREA)
            if f.shape[:2]!=(self.height,self.width) else np.asarray(f) for f in self.frames[start:stop]])
        pixels=torch.from_numpy(pixels.astype(np.float32)/255.*2.-1.).permute(0,3,1,2).contiguous()
        ctrl=encode(self.vae,pixels,self.device).float();del pixels
        torch.cuda.empty_cache()
        output=predict_video(self.model,15,ctrl,self.intr,'tiled',self.device,tile_w=22)
        native={k:np.asarray(output[k],np.float32) for k in DUMP_KEYS}
        camera=self.intr if self.given else output.get('_pred_K')
        if camera is None:raise ValueError('ACE K-free returned no predicted K')
        K=np.array([[camera['fx'],0,camera['cx']],[0,camera['fy'],camera['cy']],[0,0,1.]])
        path=self.ctx.work/f"ace_segment_{item['index']:06d}.npz"
        atomic_npz(path,go=native['global_orient'].reshape(count,2,1,3,3),hp=native['hand_pose'],
            betas=native['betas'],cam_trans=native['cam_trans'],is_right=np.tile([0.,1.],(count,1)).astype(np.float32),
            exists_2d=native['exists_2d'],exists_3d=native['exists_3d'],joints_2d=native['direct_joints2d'],
            joints_cam_direct=native['direct_joints_cam'],wrist_cam_direct=native['direct_wrist_cam'],
            frame_indices=np.arange(start,stop),K_matrix=K,run_id=np.asarray(self.ctx.run_id))
        del output,ctrl
        return path


def raster_masks(renderer,vertices,faces,camera,batch_size=4):
    """Exactly the native SoftPhong alpha calculation, omitting unused RGB shading."""
    import torch
    from pytorch3d.structures import Meshes
    result=[]
    sigma=renderer.renderer.shader.blend_params.sigma
    for start in range(0,len(vertices),batch_size):
        verts=vertices[start:start+batch_size].to('cuda');count=len(verts)
        mesh=Meshes(verts=verts,faces=faces[None].expand(count,-1,-1))
        # The installed native PyTorch3D predates CamerasBase.extend(). Rebuild
        # the same projection with repeated tensor fields, without changing K/R/T.
        params={name:getattr(camera,name) for name in ('R','T','K','focal_length','principal_point','image_size')}
        params={key:(value.expand(count,*value.shape[1:]) if torch.is_tensor(value) else value)
                for key,value in params.items()}
        cameras=type(camera)(device=camera.device,in_ndc=camera.in_ndc(),**params)
        fragments=renderer.renderer.rasterizer(mesh,cameras=cameras)
        occupied=fragments.pix_to_face>=0
        probabilities=torch.sigmoid(-fragments.dists/sigma)*occupied
        alpha=1-torch.prod(1-probabilities,dim=-1)
        result.append((alpha>0).cpu().numpy())
        del fragments,probabilities,alpha,mesh,verts
    return np.concatenate(result)


class HaworHandRunner:
    def __init__(self,ctx):
        import torch
        from .native import _hawor_numpy_compat
        _hawor_numpy_compat()
        from scripts.scripts_test_video.hawor_video import load_hawor
        from lib.vis.renderer import Renderer
        from hawor.utils.process import get_mano_faces
        self.ctx=ctx
        self.plan=json.loads((ctx.work/'hawor-plan.json').read_text())
        self.model,_=load_hawor(str(Path(ctx.spec['root'])/'weights/hawor/checkpoints/hawor.ckpt'))
        self.model=self.model.to('cuda').eval()
        w,h=self.plan['size'];f=self.plan['focal']
        self.renderer=Renderer(w,h,f,'cuda',bin_size=128,max_faces_per_bin=20000)
        self.camera,_=self.renderer.create_camera_from_cv(torch.eye(3)[None].cuda(),torch.zeros(1,3).cuda())
        faces=get_mano_faces()
        extra=np.array([[92,38,234],[234,38,239],[38,122,239],[239,122,279],[122,118,279],[279,118,215],
            [118,117,215],[215,117,214],[117,119,214],[214,119,121],[119,120,121],[121,120,78],[120,108,78],[78,108,79]])
        faces=np.concatenate([faces,extra])
        self.faces=[torch.as_tensor(faces[:,[0,2,1]],device='cuda'),torch.as_tensor(faces,device='cuda')]

    def __call__(self,item):
        import torch
        from hawor.utils.rotation import rotation_matrix_to_angle_axis,angle_axis_to_rotation_matrix
        from hawor.utils.process import run_mano,run_mano_left
        index=item['index'];task=self.plan['tasks'][index]
        frame=np.asarray(task['frames'],np.int64);slot=task['slot']
        images=[str(self.ctx.work/'input/extracted_images'/f'{i:06d}.jpg') for i in frame]
        results=self.model.inference(images,np.asarray(task['boxes'],dtype=task['box_dtype']),img_focal=self.plan['focal'],
            img_center=[self.plan['size'][0]/2,self.plan['size'][1]/2],do_flip=slot==0)
        root=rotation_matrix_to_angle_axis(results['pred_rotmat'][None,:,0])
        pose=rotation_matrix_to_angle_axis(results['pred_rotmat'][None,:,1:])
        if slot==0:root[...,1:]*=-1;pose[...,1:]*=-1
        root_matrix=angle_axis_to_rotation_matrix(root);pose_matrix=angle_axis_to_rotation_matrix(pose)
        trans=results['pred_trans'][None,:,0];betas=results['pred_shape'][None]
        # The native path converts the saved matrices back to angle-axis before MANO.
        decoded=(run_mano_left if slot==0 else run_mano)(trans,rotation_matrix_to_angle_axis(root_matrix),
            rotation_matrix_to_angle_axis(pose_matrix),betas=betas)
        vertices=decoded['vertices'][0].cpu()
        masks=raster_masks(self.renderer,vertices,self.faces[slot],self.camera,batch_size=4)
        path=self.ctx.work/'hawor-blocks'/f'{index:06d}.npz'
        def cpu(v):return v.detach().cpu().numpy()
        atomic_npz(path,root=cpu(root_matrix),pose=cpu(pose_matrix),trans=cpu(trans),betas=cpu(betas),
            masks=masks,frame_indices=frame,run_id=np.asarray(self.ctx.run_id))
        return path


def create_runner(kind,ctx):
    if kind=='ace':return AceRunner(ctx)
    if kind=='hawor_hand':return HaworHandRunner(ctx)
    from .hawor_parallel import stage_runner
    return stage_runner(kind,ctx)
