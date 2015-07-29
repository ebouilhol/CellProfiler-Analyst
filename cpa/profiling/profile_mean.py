#!/usr/bin/env python

import os
import sys
#import csv
import logging
from optparse import OptionParser
import numpy as np
from scipy.stats import mode
import cpa
from .cache import Cache
from .normalization import DummyNormalization, RobustLinearNormalization, RobustStdNormalization, normalizations
from .profiles import Profiles, add_common_options
from .parallel import ParallelProcessor, Uniprocessing

def _compute_group_mean((cache_dir, images, normalization_name, ignore_colmask,
                         preprocess_file, method)):
    try:
        import numpy as np
        from cpa.profiling.cache import Cache
        from cpa.profiling.normalization import normalizations
        from scipy.stats import norm as Gaussian
        cache = Cache(cache_dir)
        normalization = normalizations[normalization_name]
        data, colnames, _ = cache.load(images, normalization=normalization, ignore_colmask=ignore_colmask)
        #from IPython import embed; embed()
        
        cellcount = np.ones(1) * data.shape[0]
        if method == 'cellcount':
            return cellcount
        
        if len(data) == 0:
            return np.empty(len(colnames)) * np.nan

        data = data[~np.isnan(np.sum(data, 1)), :]

        if len(data) == 0:
            return np.empty(len(colnames)) * np.nan

        if preprocess_file:
            preprocessor = cpa.util.unpickle1(preprocess_file)
            data = preprocessor(data)

        if method == 'mean':
            return np.mean(data, axis=0)
        elif method == 'mean+std':
            return np.hstack((np.mean(data, axis=0), np.std(data, axis=0)))
        elif method == 'mode':
            return mode(data, axis=0)
        elif method == 'median':
            return np.median(data, axis=0)
        elif method == 'median+mad':
            c = Gaussian.ppf(3/4.)
            d = np.median(data, axis=0)
            return np.hstack((d,
                              np.median((np.fabs(data-d)) / c, axis=0)))
        elif method == 'gmm2':
            max_sample_size = 2000
            if data.shape[0] > max_sample_size:
                data = data[np.random.random_integers(0,data.shape[0]-1,size=max_sample_size),:]
            from sklearn.decomposition import PCA
            from sklearn.mixture import GMM
            pca = PCA(n_components=0.99).fit(data)
            pca_data = pca.transform(data)
            #gmm = GMM(2, covariance_type='full', n_iter=100000, thresh=1e-7).fit(pca_data)
            gmm = GMM(2, covariance_type='full').fit(pca_data)
            return pca.inverse_transform(gmm.means_).flatten()
        elif method == 'deciles':
            return np.hstack(map(lambda d: np.percentile(data, d, axis=0), range(0,101,10)))
        elif method == 'mean+deciles':
            return np.hstack((np.mean(data, axis=0), np.hstack(map(lambda d: np.percentile(data, d, axis=0), range(0,101,10)))))
    except: # catch *all* exceptions
        from traceback import print_exc
        import sys
        print_exc(None, sys.stderr)
        return None

def profile_mean(cache_dir, group_name, colnames_group, filter=None, parallel=Uniprocessing(),
                 normalization=RobustLinearNormalization, preprocess_file=None,
                 show_progress=True, method='mean',
                 full_group_header=False,
                 ignore_colmask=False):

    cache = Cache(cache_dir)
    variables = normalization(cache).colnames
    _image_table = cache.get_image_table()
    colnames_group = colnames_group.strip().split(",")
    #from IPython import embed; embed()
    
    assert len(cache.image_key_columns) == 1, "_create_cache_image does not currently support composite image_key"
    
    group = dict({})
    for idx, row in _image_table.iterrows():
        k = tuple(row[colnames_group])
        v = (row[cache.image_key_columns[0]],)
        group.setdefault(k, []).append(v)
                                                     
    keys = group.keys()
    parameters = [(cache_dir, group[g], normalization.__name__, ignore_colmask, preprocess_file, method)
                  for g in keys]

    if "CPA_DEBUG" in os.environ:
        DEBUG_NGROUPS = 5
        logging.warning('In debug mode. Using only a few groups (n=%d) to create profile' % DEBUG_NGROUPS)

        parameters = parameters[0:DEBUG_NGROUPS]
        keys = keys[0:DEBUG_NGROUPS]
    
        
    if method == 'mean+std':
        variables = variables + ['std_' + v for v in variables]
    elif method == 'median+mad':
        variables = variables + ['mad_' + v for v in variables]
    elif method == 'gmm2':
        variables = ['m1_' + v for v in variables] + ['m2_' + v for v in variables]
    elif method == 'deciles':
        variables = ['decile_%02d_%s' % (dec, v) for dec in range(0,101,10) for v in variables]
    elif method == 'mean+deciles':
        variables = variables + ['decile_%02d_%s' % (dec, v) for dec in range(0,101,10) for v in variables]
    elif method == 'cellcount':
        variables = ['Cells_Count']
    return Profiles.compute(keys, variables, _compute_group_mean, parameters,
                            parallel=parallel, group_name=group_name,
                            show_progress=show_progress, 
                            group_header=colnames_group if full_group_header else None)

    # def save_as_csv_file(self, output_file):
    #     csv_file = csv.writer(output_file)
    #     csv_file.writerow(list(self.colnames_group) + list(self.colnames))
    #     for gp, datamean in zip(self.mapping_group_images.keys(), self.results):
    #         csv_file.writerow(list(gp) + list(datamean))

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)

    parser = OptionParser("usage: %prog [options] PLATE-DIR GROUP")
    ParallelProcessor.add_options(parser)
    parser.add_option('-o', dest='output_filename', help='file to store the profiles in')
    parser.add_option('-f', dest='filter', help='only profile images matching this CPAnalyst filter')
    parser.add_option('-c', dest='csv', help='output as CSV', action='store_true')
    parser.add_option('--no-progress', dest='no_progress', help='Do not show progress bar', action='store_true')
    parser.add_option('-g', '--full-group-header', dest='full_group_header', default=False, 
                      help='Include full group header in csv file', action='store_true')
    parser.add_option('--method', dest='method', help='method: mean (default), mean+std, mode, median, median+mad, deciles, mean+deciles', 
                      action='store', default='mean')
    parser.add_option('--colnames_group', dest='colnames_group', help='colnames_group', 
                      action='store', default='Metadata_Barcode,Metadata_Well')
    parser.add_option('--ignore_colmask', dest='ignore_colmask', help='When normalizing, dont drop columns that cant be normalized', 
                      action='store_true', default=False)
                                              
    add_common_options(parser)
    options, args = parser.parse_args()
    parallel = ParallelProcessor.create_from_options(parser, options)

    if len(args) != 2:
        parser.error('Incorrect number of arguments')
    plate_dir, group = args
    cache_dir = os.path.join(plate_dir, "profiling_params")
    
    assert group == "Well", "profile_mean does not handle groups other than Well, which is currently been hard-coded"

    profiles = profile_mean(cache_dir, group, colnames_group=options.colnames_group, filter=options.filter,
                            parallel=parallel, 
                            normalization=normalizations[options.normalization],
                            ignore_colmask = options.ignore_colmask,
                            preprocess_file=options.preprocess_file,
                            method=options.method,
                            show_progress=not options.no_progress,
                            full_group_header=options.full_group_header)
    print profiles
    if options.csv:
        profiles.save_csv(options.output_filename)
    else:
        profiles.save(options.output_filename)
