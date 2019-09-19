from __future__ import absolute_import
from __future__ import print_function

import os
import pickle
import subprocess
import uuid
import xml.etree.cElementTree as ET

# import PIL
import numpy as np
import scipy.io as sio
import scipy.sparse

from lib.datasets import ds_utils
from lib.datasets.imdb import imdb
from lib.datasets.zju_eval_binary import voc_eval
# TODO: make fast_rcnn irrelevant
# >>>> obsolete, because it depends on sth outside of this project
from lib.model.utils.config import cfg


# --------------------------------------------------------
# Fast R-CNN
# Copyright (c) 2015 Microsoft
# Licensed under The MIT License [see LICENSE for details]
# Written by Ross Girshick
# --------------------------------------------------------


class zju_industry_binary(imdb):
    def __init__(self, image_set, data_path='/home/nico/Dataset/Industry-Final/',
                 p_name=None):
        imdb.__init__(self, 'industry_binary_' + image_set)
        assert image_set in ['test', 'train_supervised', 'train_unsupervised']
        self._image_set = image_set
        self._data_path = data_path

        self.p_name = p_name

        self._classes = (
            '__background__',  # always index 0
            'defect',
        )
        self._image_ext = '.jpg'
        self._image_index = self._load_image_set_index()
        # Default to roidb handler
        # self._roidb_handler = self.selective_search_roidb
        self._roidb_handler = self.gt_roidb
        self._salt = str(uuid.uuid4())
        self._comp_id = 'industry_binary'

        # PASCAL specific config options
        self.config = {'cleanup': True,
                       'use_salt': True,
                       'use_diff': False,
                       'matlab_eval': False,
                       'rpn_file': None,
                       'min_size': 2}

        assert os.path.exists(self._data_path), \
            'Dataset path does not exist: {}'.format(self._data_path)
        assert os.path.exists(self._data_path), \
            'Path does not exist: {}'.format(self._data_path)

    def image_path_at(self, i):
        """
        Return the absolute path to image i in the image sequence.
        """
        return self.image_path_from_index(self._image_index[i])

    def image_id_at(self, i):
        """
        Return the absolute path to image i in the image sequence.
        """
        return i

    def image_path_from_index(self, index):
        """
        Construct an image path from the image's "index" identifier.
        """
        image_path = os.path.join(self._data_path, 'Images',
                                  index + self._image_ext)
        assert os.path.exists(image_path), \
            'Path does not exist: {}'.format(image_path)
        return image_path

    def _load_image_set_index(self):
        """
        Load the indexes listed in this dataset's image set file.
        """
        # Example path to image set file:
        # self._devkit_path + /VOCdevkit2007/VOC2007/ImageSets/Main/val.txt
        if self.p_name:
            image_set_file = os.path.join(self._data_path, 'ImageSets',
                                          'Patterns', '%s_%s.txt' % (self.p_name, self._image_set))
        else:
            image_set_file = os.path.join(self._data_path, 'ImageSets', 'All', self._image_set + '.txt')
        assert os.path.exists(image_set_file), \
            'Path does not exist: {}'.format(image_set_file)
        with open(image_set_file) as f:
            image_index = [x.strip() for x in f.readlines()]
        return image_index

    def gt_roidb(self):
        """
        Return the database of ground-truth regions of interest.

        This function loads/saves from/to a cache file to speed up future calls.
        """
        # cache_file = os.path.join(self.cache_path, self.name + '_gt_roidb.pkl')

        # if os.path.exists(cache_file):
        #     with open(cache_file, 'rb') as fid:
        #         roidb = pickle.load(fid)
        #     print('{} gt roidb loaded from {}'.format(self.name, cache_file))
        #     return roidb

        gt_roidb = [self._load_pascal_annotation(index)
                    for index in self.image_index]
        # with open(cache_file, 'wb') as fid:
        #     pickle.dump(gt_roidb, fid, pickle.HIGHEST_PROTOCOL)
        # print('wrote gt roidb to {}'.format(cache_file))
        return gt_roidb

    def selective_search_roidb(self):
        """
        Return the database of selective search regions of interest.
        Ground-truth ROIs are also included.

        This function loads/saves from/to a cache file to speed up future calls.
        """
        cache_file = os.path.join(self.cache_path,
                                  self.name + '_selective_search_roidb.pkl')

        if os.path.exists(cache_file):
            with open(cache_file, 'rb') as fid:
                roidb = pickle.load(fid)
            print('{} ss roidb loaded from {}'.format(self.name, cache_file))
            return roidb

        if self._image_set != 'test':
            gt_roidb = self.gt_roidb()
            ss_roidb = self._load_selective_search_roidb(gt_roidb)
            roidb = imdb.merge_roidbs(gt_roidb, ss_roidb)
        else:
            roidb = self._load_selective_search_roidb(None)
        with open(cache_file, 'wb') as fid:
            pickle.dump(roidb, fid, pickle.HIGHEST_PROTOCOL)
        print('wrote ss roidb to {}'.format(cache_file))

        return roidb

    def rpn_roidb(self):
        if self._image_set != 'test':
            gt_roidb = self.gt_roidb()
            rpn_roidb = self._load_rpn_roidb(gt_roidb)
            roidb = imdb.merge_roidbs(gt_roidb, rpn_roidb)
        else:
            roidb = self._load_rpn_roidb(None)

        return roidb

    def _load_rpn_roidb(self, gt_roidb):
        filename = self.config['rpn_file']
        print('loading {}'.format(filename))
        assert os.path.exists(filename), \
            'rpn data not found at: {}'.format(filename)
        with open(filename, 'rb') as f:
            box_list = pickle.load(f)
        return self.create_roidb_from_box_list(box_list, gt_roidb)

    def _load_selective_search_roidb(self, gt_roidb):
        filename = os.path.abspath(os.path.join(cfg.DATA_DIR,
                                                'selective_search_data',
                                                self.name + '.mat'))
        assert os.path.exists(filename), \
            'Selective search data not found at: {}'.format(filename)
        raw_data = sio.loadmat(filename)['boxes'].ravel()

        box_list = []
        for i in range(raw_data.shape[0]):
            boxes = raw_data[i][:, (1, 0, 3, 2)] - 1
            keep = ds_utils.unique_boxes(boxes)
            boxes = boxes[keep, :]
            keep = ds_utils.filter_small_boxes(boxes, self.config['min_size'])
            boxes = boxes[keep, :]
            box_list.append(boxes)

        return self.create_roidb_from_box_list(box_list, gt_roidb)

    def _load_pascal_annotation(self, index):
        """
        Load image and bounding boxes info from XML file in the PASCAL VOC
        format.
        """
        filename = os.path.join(self._data_path, 'Annotations', 'xmls', index + '.xml')
        tree = ET.parse(filename)
        objs = tree.findall('bbox')
        # if not self.config['use_diff']:
        #     # Exclude the samples labeled as difficult
        #     non_diff_objs = [
        #         obj for obj in objs if int(obj.find('difficult').text) == 0]
        #     # if len(non_diff_objs) != len(objs):
        #     #     print 'Removed {} difficult objects'.format(
        #     #         len(objs) - len(non_diff_objs))
        #     objs = non_diff_objs
        num_objs = len(objs)

        boxes = np.zeros((num_objs, 4), dtype=np.uint16)
        gt_classes = np.zeros((num_objs), dtype=np.int32)
        overlaps = np.zeros((num_objs, self.num_classes), dtype=np.float32)
        # "Seg" area for pascal is just the box area
        seg_areas = np.zeros((num_objs), dtype=np.float32)

        # Load object bounding boxes into a data frame.
        for ix, bbox in enumerate(objs):
            # Make pixel indexes 0-based
            x1 = float(bbox.find('xmin').text)
            y1 = float(bbox.find('ymin').text)
            x2 = float(bbox.find('xmax').text) - 1
            y2 = float(bbox.find('ymax').text) - 1

            # cls_idx = int(tree.find('pattern').text) * int(tree.find('defective').text)
            cls_idx = int(tree.find('defective').text)
            boxes[ix, :] = [x1, y1, x2, y2]
            gt_classes[ix] = cls_idx
            overlaps[ix, cls_idx] = 1.0
            seg_areas[ix] = (x2 - x1 + 1) * (y2 - y1 + 1)

        overlaps = scipy.sparse.csr_matrix(overlaps)

        return {'boxes': boxes,
                'gt_classes': gt_classes,
                # 'gt_ishard': ishards,
                'gt_overlaps': overlaps,
                'flipped': False,
                'seg_areas': seg_areas}

    def _get_comp_id(self):
        comp_id = (self._comp_id + '_' + self._salt if self.config['use_salt']
                   else self._comp_id)
        return comp_id

    def _get_voc_results_file_template(self, output_dir):
        os.makedirs(output_dir, exist_ok=True)

        filename = self._get_comp_id() + self._image_set + \
                   '_%s_{:s}.txt' % self.p_name if self.p_name else '_{:s}.txt'
        path = os.path.join(output_dir, filename)
        return path

    def _write_voc_results_file(self, all_boxes, output_dir):
        for cls_ind, cls in enumerate(self.classes):
            if cls == '__background__':
                continue
            print('Writing {} Industry results file'.format(cls))
            filename = self._get_voc_results_file_template(output_dir).format(cls)
            with open(filename, 'wt') as f:
                for im_ind, index in enumerate(self.image_index):
                    dets = all_boxes[cls_ind][im_ind]
                    if dets == []:
                        continue
                    # the VOCdevkit expects 1-based indices
                    for k in range(dets.shape[0]):
                        f.write('{:s} {:.3f} {:.1f} {:.1f} {:.1f} {:.1f}\n'.
                                format(index, dets[k, -1],
                                       dets[k, 0] + 1, dets[k, 1] + 1,
                                       dets[k, 2] + 1, dets[k, 3] + 1))

    def _do_python_eval(self, output_dir, ovthresh=0.5):
        annopath = os.path.join(self._data_path, 'Annotations', 'xmls', '{:s}.xml')

        if self.p_name:
            imagesetfile = os.path.join(self._data_path, 'ImageSets',
                                        'Patterns', '%s_%s.txt' % (self.p_name, self._image_set))
        else:
            imagesetfile = os.path.join(self._data_path, 'ImageSets', 'All', self._image_set + '.txt')

        aps = []
        # The PASCAL VOC metric changed in 2010
        use_07_metric = True
        # print('VOC07 metric? ' + ('Yes' if use_07_metric else 'No'))

        for i, cls in enumerate(self._classes):
            if cls == '__background__':
                continue
            result_filename = self._get_voc_results_file_template(output_dir).format(cls)
            rec, prec, ap = voc_eval(
                result_filename, annopath, imagesetfile, cls, ovthresh=ovthresh,
                use_07_metric=use_07_metric)
            aps += [ap]
            # print('AP for {} = {:.4f}'.format(cls, ap))
            with open(os.path.join(output_dir, cls + '_pr.pkl'), 'wb') as f:
                pickle.dump({'rec': rec, 'prec': prec, 'ap': ap}, f)
        print('mAP@{}: {:.6f}'.format(ovthresh, np.mean(aps)))
        print('Results:')
        for ap in aps:
            print('{:.6f}'.format(ap))
        print('{:.6f}'.format(np.mean(aps)))
        # print('')
        # print('--------------------------------------------------------------')
        # print('Results computed with the **unofficial** Python eval code.')
        # print('Results should be very close to the official MATLAB eval code.')
        # print('Recompute with `./tools/reval.py --matlab ...` for your paper.')
        # print('-- Thanks, The Management')
        # print('--------------------------------------------------------------')
        return aps

    def _do_matlab_eval(self, output_dir='output'):
        print('-----------------------------------------------------')
        print('Computing results with the official MATLAB eval code.')
        print('-----------------------------------------------------')
        path = os.path.join(cfg.ROOT_DIR, 'lib', 'datasets',
                            'VOCdevkit-matlab-wrapper')
        cmd = 'cd {} && '.format(path)
        cmd += '{:s} -nodisplay -nodesktop '.format(cfg.MATLAB)
        cmd += '-r "dbstop if error; '
        cmd += 'voc_eval(\'{:s}\',\'{:s}\',\'{:s}\',\'{:s}\'); quit;"' \
            .format(self._data_path, self._get_comp_id(),
                    self._image_set, output_dir)
        print('Running:\n{}'.format(cmd))
        status = subprocess.call(cmd, shell=True)

    def evaluate_detections(self, all_boxes, output_dir, overlap_threshs=(0.5, 0.75)):
        self._write_voc_results_file(all_boxes, output_dir)
        aps = []
        for ovthresh in overlap_threshs:
            print('~~~~~~~~')
            print('Evaluating detections (overlap threshold=%g)' % ovthresh)
            ap = self._do_python_eval(output_dir, ovthresh)
            print('~~~~~~~~')
            # for binary classification, the length of ap list is 1
            aps.append(ap[0])
        return aps

    def competition_mode(self, on):
        if on:
            self.config['use_salt'] = False
            self.config['cleanup'] = False
        else:
            self.config['use_salt'] = True
            self.config['cleanup'] = True


if __name__ == '__main__':
    d = zju_industry_binary('train_supervised')
    res = d.roidb
    from IPython import embed

    embed()
