import os
import numpy as np
import nibabel as nb
import pickle
import matplotlib.pyplot as plt
from rabies.analysis_pkg import analysis_functions, prior_modeling
from nilearn.plotting import plot_stat_map
import SimpleITK as sitk

# import torch dependencies for prior_modeling
import torch
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")


'''
Workflow structure:
Step 1: resample the WM/CSF/GM/right/left hem masks and the group-ICA onto subject space, or if commonspace only do it once onto the template
*make it as an option to select the DSURQE-generated regional masks or use the WM/CSF from the inputs
Step 2: prep the subject-level data
Step 3: generate teh subject-level figures
Step 4: run the group level
'''

from nipype.interfaces.base import (
    traits, TraitedSpec, BaseInterfaceInputSpec,
    File, BaseInterface
)


class PrepMasksInputSpec(BaseInterfaceInputSpec):
    mask_dict_list = traits.List(exists=True, mandatory=True, desc="Brain mask.")
    prior_maps = File(exists=True, mandatory=True, desc="MELODIC ICA components to use.")
    DSURQE_regions = traits.Bool(
        desc="Whether to use the regional masks generated from the DSURQE atlas for the grayplots outputs. Requires using the DSURQE template for preprocessing.")
'''
    preprocess_anat_template = File(exists=True, mandatory=True, desc="The common space template used during preprocessing.")
    brain_mask_file_list = traits.List(exists=True, mandatory=True, desc="Brain mask.")
    WM_mask_file = File(exists=True, mandatory=True, desc="WM mask.")
    CSF_mask_file = File(exists=True, mandatory=True, desc="CSF mask.")
'''

class PrepMasksOutputSpec(TraitedSpec):
    mask_file_dict = traits.Dict(desc="A dictionary regrouping the all required accompanying files.")

class PrepMasks(BaseInterface):
    """

    """

    input_spec = PrepMasksInputSpec
    output_spec = PrepMasksOutputSpec

    def _run_interface(self, runtime):
        from rabies.preprocess_pkg.utils import flatten_list
        merged = flatten_list(list(self.inputs.mask_dict_list))
        mask_dict = merged[0] # all mask files are assumed to be identical
        brain_mask_file = mask_dict['brain_mask_file']
        WM_mask_file = mask_dict['WM_mask_file']
        CSF_mask_file = mask_dict['CSF_mask_file']


        # resample the template to the EPI dimensions
        from rabies.preprocess_pkg.utils import resample_image_spacing
        resampled = resample_image_spacing(sitk.ReadImage(mask_dict['preprocess_anat_template']), sitk.ReadImage(brain_mask_file).GetSpacing(), resampling_interpolation='BSpline')
        template_file = os.path.abspath('display_template.nii.gz')
        sitk.WriteImage(resampled,template_file)

        if self.inputs.DSURQE_regions:
            right_hem_mask_file = resample_mask(os.environ['RABIES']+'/template_files/DSURQE_100micron_right_hem_mask.nii.gz',
                    brain_mask_file)
            left_hem_mask_file = resample_mask(os.environ['RABIES']+'/template_files/DSURQE_100micron_left_hem_mask.nii.gz',
                    brain_mask_file)
        else:
            right_hem_mask_file=''
            left_hem_mask_file=''

        prior_maps = resample_IC_file(self.inputs.prior_maps, brain_mask_file)

        edge_mask_file = os.path.abspath('edge_mask.nii.gz')
        compute_edge_mask(brain_mask_file,edge_mask_file, num_edge_voxels=1)
        mask_file_dict = {'template_file':template_file, 'brain_mask':brain_mask_file, 'WM_mask':WM_mask_file, 'CSF_mask':CSF_mask_file, 'edge_mask':edge_mask_file, 'right_hem_mask':right_hem_mask_file, 'left_hem_mask':left_hem_mask_file, 'prior_maps':prior_maps}

        setattr(self, 'mask_file_dict', mask_file_dict)
        return runtime

    def _list_outputs(self):
        return {'mask_file_dict': getattr(self, 'mask_file_dict')}

class ScanDiagnosisInputSpec(BaseInterfaceInputSpec):
    file_dict = traits.Dict(desc="A dictionary regrouping the all required accompanying files.")
    mask_file_dict = traits.Dict(desc="A dictionary regrouping the all required accompanying files.")
    prior_bold_idx = traits.List(desc="The index for the ICA components that correspond to bold sources.")
    prior_confound_idx = traits.List(desc="The index for the ICA components that correspond to confounding sources.")
    dual_regression = traits.Bool(
        desc="Whether to evaluate dual regression outputs.")
    dual_convergence = traits.Int(
        desc="number of components to compute from dual convergence.")
    DSURQE_regions = traits.Bool(
        desc="Whether to use the regional masks generated from the DSURQE atlas for the grayplots outputs. Requires using the DSURQE template for preprocessing.")


class ScanDiagnosisOutputSpec(TraitedSpec):
    figure_temporal_diagnosis = File(
        exists=True, desc="Output figure from the scan diagnosis")
    figure_spatial_diagnosis = File(
        exists=True, desc="Output figure from the scan diagnosis")
    temporal_info = traits.Dict(desc="A dictionary regrouping the temporal features.")
    spatial_info = traits.Dict(desc="A dictionary regrouping the spatial features.")

class ScanDiagnosis(BaseInterface):
    """

    """

    input_spec = ScanDiagnosisInputSpec
    output_spec = ScanDiagnosisOutputSpec

    def _run_interface(self, runtime):
        # convert to an integer list
        bold_file = self.inputs.file_dict['bold_file']
        CR_data_dict = self.inputs.file_dict['CR_data_dict']
        prior_bold_idx = [int(i) for i in self.inputs.prior_bold_idx]
        prior_confound_idx = [int(i) for i in self.inputs.prior_confound_idx]

        if self.inputs.dual_convergence>0:
            num_comp=self.inputs.dual_convergence
            convergence_function='ICA'
            prior_fit_options=[num_comp,convergence_function]
            prior_fit=True
        else:
            prior_fit=False
            prior_fit_options=[]

        temporal_info,spatial_info = process_data(bold_file, CR_data_dict, self.inputs.mask_file_dict, prior_bold_idx, prior_confound_idx, self.inputs.dual_regression, prior_fit=prior_fit,prior_fit_options=prior_fit_options)

        fig,fig2 = scan_diagnosis(bold_file,self.inputs.mask_file_dict,temporal_info,spatial_info, regional_grayplot=self.inputs.DSURQE_regions)

        import pathlib
        filename_template = pathlib.Path(bold_file).name.rsplit(".nii")[0]
        figure_path = os.path.abspath(filename_template)
        fig.savefig(figure_path+'_temporal_diagnosis.png', bbox_inches='tight')
        fig2.savefig(figure_path+'_spatial_diagnosis.png', bbox_inches='tight')

        setattr(self, 'figure_temporal_diagnosis', figure_path+'_temporal_diagnosis.png')
        setattr(self, 'figure_spatial_diagnosis', figure_path+'_spatial_diagnosis.png')
        setattr(self, 'temporal_info', temporal_info)
        setattr(self, 'spatial_info', spatial_info)

        return runtime

    def _list_outputs(self):
        return {'figure_temporal_diagnosis': getattr(self, 'figure_temporal_diagnosis'),
                'figure_spatial_diagnosis': getattr(self, 'figure_spatial_diagnosis'),
                'temporal_info': getattr(self, 'temporal_info'),
                'spatial_info': getattr(self, 'spatial_info'), }


class DatasetDiagnosisInputSpec(BaseInterfaceInputSpec):
    spatial_info_list = traits.List(exists=True, mandatory=True, desc="A dictionary regrouping the spatial features.")
    mask_file_dict = traits.Dict(exists=True, mandatory=True, desc="A dictionary regrouping the all required accompanying files.")


class DatasetDiagnosisOutputSpec(TraitedSpec):
    figure_dataset_diagnosis = File(
        exists=True, desc="Output figure from the dataset diagnosis")


class DatasetDiagnosis(BaseInterface):
    """

    """

    input_spec = DatasetDiagnosisInputSpec
    output_spec = DatasetDiagnosisOutputSpec

    def _run_interface(self, runtime):
        from rabies.preprocess_pkg.utils import flatten_list
        merged = flatten_list(list(self.inputs.spatial_info_list))
        if len(merged)<3:
            raise ValueError("Cannot run statistics on a sample size smaller than 3.")

        dict_keys=['temporal_std','VE_spatial','GS_corr','DVARS_corr','FD_corr','DR_maps', 'prior_modeling_maps']

        voxelwise_list=[]
        for spatial_info in merged:
            sub_list = [spatial_info[key] for key in dict_keys]
            voxelwise_sub = np.array(sub_list[:5])
            voxelwise_sub = np.concatenate((voxelwise_sub,np.array(sub_list[5]),np.array(sub_list[6])),axis=0)
            voxelwise_list.append(voxelwise_sub)
            num_DR_maps = len(sub_list[5])
            num_prior_maps = len(sub_list[6])
        voxelwise_array=np.array(voxelwise_list)

        label_name=['temporal_std','VE_spatial','GS_corr','DVARS_corr','FD_corr']
        label_name += ['BOLD DR map %s' % (i) for i in range(num_DR_maps)]
        label_name += ['BOLD prior modeling map %s' % (i) for i in range(num_prior_maps)]


        template_file = self.inputs.mask_file_dict['template_file']
        mask_file = self.inputs.mask_file_dict['brain_mask']
        from rabies.preprocess_pkg.preprocess_visual_QC import plot_coronal, otsu_scaling
        scaled = otsu_scaling(template_file)

        ncols=5
        fig,axes = plt.subplots(nrows=voxelwise_array.shape[1], ncols=ncols,figsize=(12*ncols,3*voxelwise_array.shape[1]))
        for i,x_label in zip(range(voxelwise_array.shape[1]),label_name):
            for j,y_label in zip(range(ncols),label_name[:ncols]):
                ax=axes[i,j]
                if i<=j:
                    ax.axis('off')
                    continue

                X=voxelwise_array[:,i,:]
                Y=voxelwise_array[:,j,:]
                corr=elementwise_corrcoef(X, Y)

                plot_coronal(ax,scaled,fig,vmin=0,vmax=1,cmap='gray', alpha=1, cbar=False)
                analysis_functions.recover_3D(mask_file,corr).to_filename('temp_img.nii.gz')
                sitk_img=sitk.ReadImage('temp_img.nii.gz')
                plot_coronal(ax,sitk_img,fig,vmin=-0.7,vmax=0.7,cmap='cold_hot', alpha=1, cbar=True, threshold=0.1)
                ax.set_title('Cross-correlation for %s and %s' % (x_label,y_label), fontsize=15)
        fig.savefig(os.path.abspath('dataset_diagnosis.png'), bbox_inches='tight')

        setattr(self, 'figure_dataset_diagnosis', os.path.abspath('dataset_diagnosis.png'))
        return runtime

    def _list_outputs(self):
        return {'figure_dataset_diagnosis': getattr(self, 'figure_dataset_diagnosis')}

def resample_mask(in_file, ref_file):
    transforms=[]
    inverses=[]
    # resampling the reference image to the dimension of the EPI
    from rabies.preprocess_pkg.utils import run_command
    import pathlib  # Better path manipulation
    filename_split = pathlib.Path(
        in_file).name.rsplit(".nii")
    out_file = os.path.abspath(filename_split[0])+'_resampled.nii.gz'

    # tranforms is a list of transform files, set in order of call within antsApplyTransforms
    transform_string = ""
    for transform, inverse in zip(transforms, inverses):
        if transform=='NULL':
            continue
        elif bool(inverse):
            transform_string += "-t [%s,1] " % (transform,)
        else:
            transform_string += "-t %s " % (transform,)

    command = 'antsApplyTransforms -i %s %s-n GenericLabel -r %s -o %s' % (
        in_file, transform_string, ref_file, out_file)
    rc = run_command(command)
    return out_file


def resample_IC_file(in_file, ref_file):
    transforms=[]
    inverses=[]
    # resampling the reference image to the dimension of the EPI
    import SimpleITK as sitk
    import os
    from rabies.preprocess_pkg.utils import run_command,split_volumes,copyInfo_4DImage
    import pathlib  # Better path manipulation
    filename_split = pathlib.Path(
        in_file).name.rsplit(".nii")
    out_file = os.path.abspath(filename_split[0])+'_resampled.nii.gz'

    # tranforms is a list of transform files, set in order of call within antsApplyTransforms
    transform_string = ""
    for transform, inverse in zip(transforms, inverses):
        if transform=='NULL':
            continue
        elif bool(inverse):
            transform_string += "-t [%s,1] " % (transform,)
        else:
            transform_string += "-t %s " % (transform,)

    # Splitting bold file into lists of single volumes
    [volumes_list, num_volumes] = split_volumes(
        in_file, "bold_", sitk.sitkFloat32)

    warped_volumes = []
    for x in range(0, num_volumes):
        warped_vol_fname = os.path.abspath(
            "deformed_volume" + str(x) + ".nii.gz")
        warped_volumes.append(warped_vol_fname)

        command = 'antsApplyTransforms -i %s %s-n BSpline[5] -r %s -o %s' % (
            volumes_list[x], transform_string, ref_file, warped_vol_fname)
        rc = run_command(command)


    sample_volume = sitk.ReadImage(
        warped_volumes[0])
    shape = sitk.GetArrayFromImage(sample_volume).shape
    combined = np.zeros((num_volumes, shape[0], shape[1], shape[2]))

    i = 0
    for file in warped_volumes:
        combined[i, :, :, :] = sitk.GetArrayFromImage(
            sitk.ReadImage(file))[:, :, :]
        i = i+1
    if (i != num_volumes):
        raise ValueError("Error occured with Merge.")

    combined_image = sitk.GetImageFromArray(combined, isVector=False)

    # set metadata and affine for the newly constructed 4D image
    header_source = sitk.ReadImage(
        in_file)
    combined_image = copyInfo_4DImage(
        combined_image, sample_volume, header_source)
    sitk.WriteImage(combined_image, out_file)
    return out_file


def compute_edge_mask(in_mask,out_file, num_edge_voxels=1):
    #custom function for computing edge mask from an input brain mask
    img=nb.load(in_mask)
    mask_array=np.asarray(img.dataobj)
    shape=mask_array.shape

    #iterate through all voxels from the three dimensions and look if it contains surrounding voxels
    edge_mask=np.zeros(shape, dtype=bool)
    num_voxel=0
    while num_voxel<num_edge_voxels:
        for x in range(shape[0]):
            for y in range(shape[1]):
                for z in range(shape[2]):
                    #only look if the voxel is part of the mask
                    if mask_array[x,y,z]:
                        if (mask_array[x-1:x+2,y-1:y+2,z-1:z+2]==0).sum()>0:
                            edge_mask[x,y,z]=1
        mask_array=mask_array-edge_mask
        num_voxel+=1

    nb.Nifti1Image(edge_mask, img.affine, img.header).to_filename(out_file)

'''
Prepare the subject data
'''

def process_data(bold_file, data_dict, mask_file_dict, prior_bold_idx, prior_confound_idx, dual_regression=False, prior_fit=False,prior_fit_options=[]):
    temporal_info={}
    spatial_info={}

    FD_trace = data_dict['FD_trace']
    DVARS = data_dict['DVARS']
    temporal_info['FD_trace'] = FD_trace
    temporal_info['DVARS'] = DVARS
    temporal_info['VE_temporal'] = data_dict['VE_temporal']
    spatial_info['VE_spatial'] = data_dict['VE_spatial']

    WM_mask=np.asarray(nb.load(mask_file_dict['WM_mask']).dataobj).astype(bool)
    CSF_mask=np.asarray(nb.load(mask_file_dict['CSF_mask']).dataobj).astype(bool)

    edge_mask=np.asarray(nb.load(mask_file_dict['edge_mask']).dataobj).astype(bool)
    brain_mask=np.asarray(nb.load(mask_file_dict['brain_mask']).dataobj)
    volume_indices=brain_mask.astype(bool)
    edge_idx=edge_mask[volume_indices]
    WM_idx = WM_mask[volume_indices]
    CSF_idx = CSF_mask[volume_indices]
    not_edge_idx=(edge_idx==0)*(WM_idx==0)*(CSF_idx==0)

    data_array=np.asarray(nb.load(bold_file).dataobj)
    timeseries=np.zeros([data_array.shape[3],volume_indices.sum()])
    for i in range(data_array.shape[3]):
        timeseries[i,:]=(data_array[:,:,:,i])[volume_indices]

    all_IC_array=np.asarray(nb.load(mask_file_dict['prior_maps']).dataobj)
    all_IC_vectors=np.zeros([all_IC_array.shape[3],volume_indices.sum()])
    for i in range(all_IC_array.shape[3]):
        all_IC_vectors[i,:]=(all_IC_array[:,:,:,i])[volume_indices]


    '''Temporal Features'''
    X = all_IC_vectors.T
    Y = timeseries.T
    # for one given volume, it's values can be expressed through a linear combination of the components
    w = analysis_functions.closed_form(X, Y, intercept=True) # take a bias into account in the model
    w = w[:-1,:] # take out the intercept

    signal_trace = np.abs(w[prior_bold_idx,:]).mean(axis=0)
    noise_trace = np.abs(w[prior_confound_idx,:]).mean(axis=0)
    temporal_info['signal_trace']=signal_trace
    temporal_info['noise_trace']=noise_trace

    # take regional timecourse from L2-norm
    global_trace=np.sqrt((timeseries.T**2).mean(axis=0))
    WM_trace=np.sqrt((timeseries.T[WM_idx]**2).mean(axis=0))
    CSF_trace=np.sqrt((timeseries.T[CSF_idx]**2).mean(axis=0))
    edge_trace=np.sqrt((timeseries.T[edge_idx]**2).mean(axis=0))
    not_edge_trace=np.sqrt((timeseries.T[not_edge_idx]**2).mean(axis=0))
    temporal_info['global_trace']=global_trace
    temporal_info['WM_trace']=WM_trace
    temporal_info['CSF_trace']=CSF_trace
    temporal_info['edge_trace']=edge_trace
    temporal_info['not_edge_trace']=not_edge_trace

    '''Spatial Features'''
    temporal_std=timeseries.std(axis=0)
    global_signal = timeseries.mean(axis=1)
    GS_corr = analysis_functions.vcorrcoef(timeseries.T, global_signal)
    DVARS_corr = analysis_functions.vcorrcoef(timeseries.T[:,1:], DVARS[1:])
    FD_corr = analysis_functions.vcorrcoef(timeseries.T, np.asarray(FD_trace))

    if dual_regression:
        dr_maps = analysis_functions.dual_regression(all_IC_vectors, timeseries)
        signal_maps=dr_maps[prior_bold_idx]
    else:
        signal_maps=np.empty([0,len(temporal_std)])

    prior_out=np.empty([0,len(temporal_std)])
    if prior_fit:
        prior_out = []
        [num_comp,convergence_function] = prior_fit_options
        X=torch.tensor(timeseries).float().to(device)

        prior_networks = torch.tensor(all_IC_vectors[prior_bold_idx,:].T).float().to(device)

        C_prior=prior_networks
        C_conf = prior_modeling.deflation_fit(X, q=num_comp, c_init=None, C_convergence=convergence_function,
                          C_prior=C_prior, W_prior=None, W_ortho=True, tol=1e-6, max_iter=200, verbose=1)
        for network in range(prior_networks.shape[1]):
            prior=prior_networks[:,network].reshape(-1,1)
            C_prior=torch.cat((prior_networks[:,:network],prior_networks[:,network+1:],C_conf),axis=1)

            C_fit = prior_modeling.deflation_fit(X, q=1, c_init=prior, C_convergence=convergence_function,
                                  C_prior=C_prior, W_prior=None, W_ortho=True, tol=1e-6, max_iter=200, verbose=1)

            # make sure the sign of weights is the same as the prior
            corr = np.corrcoef(C_fit.cpu().flatten(), prior.cpu().flatten())[0, 1]
            if corr < 0:
                C_fit = C_fit*-1

            # the finalized C
            C = torch.cat((C_fit, C_prior), axis=1)

            # L-2 norm normalization of the components
            C /= torch.sqrt((C ** 2).sum(axis=0))
            W = prior_modeling.torch_closed_form(C,X.T).T
            C_norm=C*W.std(axis=0)

            prior_out.append(C_norm[:,0].cpu().numpy())

    spatial_info['temporal_std'] = temporal_std
    spatial_info['GS_corr'] = GS_corr
    spatial_info['DVARS_corr'] = DVARS_corr
    spatial_info['FD_corr'] = FD_corr
    spatial_info['DR_maps'] = signal_maps
    spatial_info['prior_modeling_maps'] = prior_out

    return temporal_info,spatial_info


def spatial_external_formating(spatial_info, file_dict):
    import os
    import pathlib  # Better path manipulation
    from rabies.analysis_pkg import analysis_functions
    mask_file = file_dict['brain_mask_file']
    bold_file = file_dict['bold_file']
    filename_split = pathlib.Path(
        bold_file).name.rsplit(".nii")

    # calculate STD and tSNR map on preprocessed timeseries
    std_filename = os.path.abspath(filename_split[0]+'_tSTD.nii.gz')
    analysis_functions.recover_3D(mask_file,spatial_info['temporal_std']).to_filename(std_filename)

    GS_corr_filename = os.path.abspath(filename_split[0]+'_GS_corr.nii.gz')
    analysis_functions.recover_3D(mask_file,spatial_info['GS_corr']).to_filename(GS_corr_filename)

    DVARS_corr_filename = os.path.abspath(filename_split[0]+'_DVARS_corr.nii.gz')
    analysis_functions.recover_3D(mask_file,spatial_info['DVARS_corr']).to_filename(DVARS_corr_filename)

    FD_corr_filename = os.path.abspath(filename_split[0]+'_FD_corr.nii.gz')
    analysis_functions.recover_3D(mask_file,spatial_info['FD_corr']).to_filename(FD_corr_filename)

    if len(spatial_info['DR_maps'])>0:
        DR_maps_filename = os.path.abspath(filename_split[0]+'_DR_maps.nii.gz')
        analysis_functions.recover_3D_multiple(mask_file,spatial_info['DR_maps']).to_filename(DR_maps_filename)
    else:
        DR_maps_filename = None

    if len(spatial_info['prior_modeling_maps'])>0:
        import numpy as np
        prior_modeling_filename = os.path.abspath(filename_split[0]+'_prior_modeling.nii.gz')
        analysis_functions.recover_3D_multiple(mask_file,np.array(spatial_info['prior_modeling_maps'])).to_filename(prior_modeling_filename)
    else:
        prior_modeling_filename = None

    return std_filename, GS_corr_filename, DVARS_corr_filename, FD_corr_filename, DR_maps_filename, prior_modeling_filename


'''
Subject-level QC
'''

def grayplot_regional(timeseries_file,mask_file_dict,fig,ax):
    timeseries_4d=np.asarray(nb.load(timeseries_file).dataobj)

    WM_mask=np.asarray(nb.load(mask_file_dict['WM_mask']).dataobj).astype(bool)
    CSF_mask=np.asarray(nb.load(mask_file_dict['CSF_mask']).dataobj).astype(bool)
    right_hem_mask=np.asarray(nb.load(mask_file_dict['right_hem_mask']).dataobj).astype(bool)
    left_hem_mask=np.asarray(nb.load(mask_file_dict['left_hem_mask']).dataobj).astype(bool)

    grayplot_array=np.empty((0,timeseries_4d.shape[3]))
    slice_alt=np.array([])
    region_mask_label=np.array([])
    c=0
    for mask_indices in [right_hem_mask,left_hem_mask,WM_mask,CSF_mask]:
        region_mask_label=np.append(region_mask_label,np.ones(mask_indices.sum())*c)
        c+=1
        token=False
        for i in range(mask_indices.shape[1]):
            grayplot_array=np.append(grayplot_array,timeseries_4d[:,i,:,:][mask_indices[:,i,:]],axis=0)
            slice_alt=np.append(slice_alt,np.ones(mask_indices[:,i,:].sum())*token)
            token= not token

    vmax=grayplot_array.std()
    im = ax.imshow(grayplot_array, cmap='gray', vmax=vmax, vmin=-vmax, aspect='auto')
    return im,slice_alt,region_mask_label


def grayplot(timeseries_file,mask_file_dict,fig,ax):
    brain_mask=np.asarray(nb.load(mask_file_dict['brain_mask']).dataobj)
    volume_indices=brain_mask.astype(bool)

    data_array=np.asarray(nb.load(timeseries_file).dataobj)
    timeseries=np.zeros([data_array.shape[3],volume_indices.sum()])
    for i in range(data_array.shape[3]):
        timeseries[i,:]=(data_array[:,:,:,i])[volume_indices]

    grayplot_array=timeseries.T

    vmax=grayplot_array.std()
    im = ax.imshow(grayplot_array, cmap='gray', vmax=vmax, vmin=-vmax, aspect='auto')
    return im

def scan_diagnosis(bold_file,mask_file_dict,temporal_info,spatial_info, regional_grayplot=False):
    template_file = mask_file_dict['template_file']
    from mpl_toolkits.axes_grid1 import make_axes_locatable
    fig, axCenter = plt.subplots(figsize=(6,15))
    fig.subplots_adjust(.2,.1,.8,.95)

    divider = make_axes_locatable(axCenter)
    ax1 = divider.append_axes('bottom', size='50%', pad=0.5)
    ax2 = divider.append_axes('bottom', size='50%', pad=0.5)
    ax3 = divider.append_axes('bottom', size='50%', pad=0.5)

    if regional_grayplot:
        im,slice_alt,region_mask_label = grayplot_regional(bold_file,mask_file_dict,fig,axCenter)
        axCenter.yaxis.labelpad = 40
        ax_slice = divider.append_axes('left', size='5%', pad=0.0)
        ax_label = divider.append_axes('left', size='5%', pad=0.0)

        ax_slice.imshow(slice_alt.reshape(-1,1), cmap='gray', vmin=0, vmax=1.1, aspect='auto')
        ax_label.imshow(region_mask_label.reshape(-1,1), cmap='Spectral', aspect='auto')
        ax_slice.axis('off')
        ax_label.axis('off')

    else:
        im=grayplot(bold_file,mask_file_dict,fig,axCenter)

    axCenter.set_ylabel('Voxels', fontsize=20)
    axCenter.spines['right'].set_visible(False)
    axCenter.spines['top'].set_visible(False)
    axCenter.spines['bottom'].set_visible(False)
    axCenter.spines['left'].set_visible(False)
    axCenter.axes.get_yaxis().set_ticks([])
    plt.setp(axCenter.get_xticklabels(), visible=False)

    #ax1.set_title(name, fontsize=15)
    y=temporal_info['FD_trace']
    ax1.plot(y, 'r')
    ax1.set_xlim([0,len(y)])
    ax1.legend(['Framewise Displacement (FD)'], loc='upper right')
    ax1.spines['right'].set_visible(False)
    ax1.spines['top'].set_visible(False)
    ax1.set_ylim([0.0,0.1])
    plt.setp(ax1.get_xticklabels(), visible=False)

    DVARS=temporal_info['DVARS']
    DVARS[0]=None
    y=DVARS
    ax2.plot(y)
    ax2.set_xlim([0,len(y)])
    ax2.plot(temporal_info['edge_trace'])
    ax2.plot(temporal_info['WM_trace'])
    ax2.plot(temporal_info['CSF_trace'])
    #ax2.plot(temporal_info['not_edge_trace'])
    ax2.plot(temporal_info['VE_temporal'])
    #ax2.set_ylabel('Mask L2-norm', fontsize=12)
    ax2.legend(['DVARS','Edge Mask', 'WM Mask', 'CSF Mask', 'CR R^2'], loc='center left', bbox_to_anchor=(1, 0.5))
    ax2.spines['right'].set_visible(False)
    ax2.spines['top'].set_visible(False)
    ax2.set_ylim([0.0,1])
    plt.setp(ax2.get_xticklabels(), visible=False)

    y=temporal_info['signal_trace']
    ax3.plot(y)
    ax3.set_xlim([0,len(y)])
    ax3.plot(temporal_info['noise_trace'])
    #ax3.set_ylabel('Mean Component Weight', fontsize=12)
    ax3.legend(['BOLD components', 'Confound components'], loc='upper right')
    ax3.spines['right'].set_visible(False)
    ax3.spines['top'].set_visible(False)
    ax3.set_ylim([0.0,0.09])
    ax3.set_xlabel('Timepoint', fontsize=15)

    dr_maps=spatial_info['DR_maps']
    mask_file=mask_file_dict['brain_mask']

    nrows=5+dr_maps.shape[0]

    fig2,axes2 = plt.subplots(nrows=nrows, ncols=3,figsize=(12*3,3*nrows))
    plt.tight_layout()

    from rabies.preprocess_pkg.preprocess_visual_QC import plot_3d, otsu_scaling

    axes=axes2[0,:]
    scaled = otsu_scaling(template_file)
    plot_3d(axes,scaled,fig2,vmin=0,vmax=1,cmap='gray', alpha=1, cbar=False)
    analysis_functions.recover_3D(mask_file,spatial_info['temporal_std']).to_filename('temp_img.nii.gz')
    sitk_img=sitk.ReadImage('temp_img.nii.gz')
    plot_3d(axes,sitk_img,fig2,vmin=0,vmax=1,cmap='inferno', alpha=1, cbar=True)
    for ax in axes:
        ax.set_title('Temporal STD', fontsize=25)

    axes=axes2[1,:]
    plot_3d(axes,scaled,fig2,vmin=0,vmax=1,cmap='gray', alpha=1, cbar=False)
    analysis_functions.recover_3D(mask_file,spatial_info['VE_spatial']).to_filename('temp_img.nii.gz')
    sitk_img=sitk.ReadImage('temp_img.nii.gz')
    plot_3d(axes,sitk_img,fig2,vmin=0,vmax=1,cmap='inferno', alpha=1, cbar=True, threshold=0.1)
    for ax in axes:
        ax.set_title('CR R^2', fontsize=25)

    axes=axes2[2,:]
    plot_3d(axes,scaled,fig2,vmin=0,vmax=1,cmap='gray', alpha=1, cbar=False)
    analysis_functions.recover_3D(mask_file,spatial_info['GS_corr']).to_filename('temp_img.nii.gz')
    sitk_img=sitk.ReadImage('temp_img.nii.gz')
    plot_3d(axes,sitk_img,fig2,vmin=-1,vmax=1,cmap='cold_hot', alpha=1, cbar=True, threshold=0.1)
    for ax in axes:
        ax.set_title('Global Signal Correlation', fontsize=25)

    axes=axes2[3,:]
    plot_3d(axes,scaled,fig2,vmin=0,vmax=1,cmap='gray', alpha=1, cbar=False)
    analysis_functions.recover_3D(mask_file,spatial_info['DVARS_corr']).to_filename('temp_img.nii.gz')
    sitk_img=sitk.ReadImage('temp_img.nii.gz')
    plot_3d(axes,sitk_img,fig2,vmin=-1,vmax=1,cmap='cold_hot', alpha=1, cbar=True, threshold=0.1)
    for ax in axes:
        ax.set_title('DVARS Correlation', fontsize=25)

    axes=axes2[4,:]
    plot_3d(axes,scaled,fig2,vmin=0,vmax=1,cmap='gray', alpha=1, cbar=False)
    analysis_functions.recover_3D(mask_file,spatial_info['FD_corr']).to_filename('temp_img.nii.gz')
    sitk_img=sitk.ReadImage('temp_img.nii.gz')
    plot_3d(axes,sitk_img,fig2,vmin=-1,vmax=1,cmap='cold_hot', alpha=1, cbar=True, threshold=0.1)
    for ax in axes:
        ax.set_title('FD Correlation', fontsize=25)

    for i in range(dr_maps.shape[0]):
        axes=axes2[i+5,:]
        plot_3d(axes,scaled,fig2,vmin=0,vmax=1,cmap='gray', alpha=1, cbar=False)
        analysis_functions.recover_3D(mask_file,dr_maps[i,:]).to_filename('temp_img.nii.gz')
        sitk_img=sitk.ReadImage('temp_img.nii.gz')
        plot_3d(axes,sitk_img,fig2,vmin=-1,vmax=1,cmap='cold_hot', alpha=1, cbar=True, threshold=0.1)
        for ax in axes:
            ax.set_title('BOLD component %s' % (i), fontsize=25)

    return fig,fig2


'''
Dataset-level QC
'''

def elementwise_corrcoef(X, Y):
    # X and Y are each of shape num_observations X num_element
    # computes the correlation between each element of X and Y
    Xm = X.mean(axis=0)
    Ym = Y.mean(axis=0)
    r_num = np.sum((X-Xm)*(Y-Ym), axis=0)

    r_den = np.sqrt(np.sum((X-Xm)**2, axis=0)*np.sum((Y-Ym)**2, axis=0))
    r = r_num/r_den
    return r


'''

def closed_form_3d(X,Y):
    return np.matmul(np.matmul(np.linalg.inv(np.matmul(X.transpose(0,2,1),X)),X.transpose(0,2,1)),Y)

def lme_stats_3d(X,Y):
    #add an intercept
    X=np.concatenate((X,np.ones((X.shape[0],X.shape[1],1))),axis=2)
    [num_comparisons,num_observations,num_predictors] = X.shape
    [num_comparisons,num_observations,num_features] = Y.shape

    w=closed_form_3d(X,Y)

    residuals = Y-np.matmul(X, w)
    MSE = (((residuals)**2).sum(axis=1)/(num_observations-num_predictors))


    var_b = np.expand_dims(MSE, axis=1)*np.expand_dims(np.linalg.inv(np.matmul(X.transpose(0,2,1),X)).diagonal(axis1=1,axis2=2), axis=2)
    sd_b = np.sqrt(var_b) # standard error on the Betas
    ts_b = w/sd_b # calculate t-values for the Betas
    p_values =[2*(1-stats.t.cdf(np.abs(ts_b[:,i,:]),(num_observations-num_predictors))) for i in range(ts_b.shape[1])] # calculate a p-value map for each predictor

    return ts_b,p_values,w,residuals

non_nan_idx = (np.isnan(voxelwise_array).sum(axis=(0,1))==0)

# take out the voxels which have null values
X=voxelwise_array[:,:6,non_nan_idx].transpose(2,0,1)
Y=voxelwise_array[:,6:,non_nan_idx].transpose(2,0,1)


ts_b,p_values,w,residuals = lme_stats_3d(X,Y)

x_name=['Somatomotor','Dorsal Comp','DMN', 'Prior Modeling 1', 'Prior Modeling 2', 'Prior Modeling 3']
y_name=['group','temporal_std','VE_spatial','GS_corr','DVARS_corr','FD_corr']

fig,axes = plt.subplots(nrows=len(x_name), ncols=len(y_name),figsize=(12*len(y_name),3*len(x_name)))


for i,x_label in zip(range(len(x_name)),x_name):
    for j,y_label in zip(range(len(y_name)),y_name):
        ax=axes[i,j]

        stat_map=np.zeros(voxelwise_array.shape[2])
        stat_map[non_nan_idx]=ts_b[:,j,i]

        ax.set_title('T-value of %s on %s' % (y_label,x_label), fontsize=15)
        plot_stat_map(analysis_functions.recover_3D(mask_file,stat_map),bg_img='DSURQE.nii.gz', axes=ax, cut_coords=(0,1,2,3,4,5), display_mode='y')
'''
