"""Pixel-grid transformations shared by native dataset preparation and tests."""
from __future__ import annotations

import numpy as np


def pinhole_matrix(value):
    K = np.asarray(value, np.float64)
    if K.shape != (3, 3) or not np.isfinite(K).all() or min(K[0, 0], K[1, 1]) <= 0:
        raise ValueError('Calibration requires a finite positive 3x3 pixel matrix')
    if not np.allclose(K[2], [0, 0, 1]) or not np.allclose(K[[0, 1], [1, 0]], 0):
        raise ValueError('Only a zero-skew pinhole matrix is supported')
    return K.copy()


def resize_intrinsics(K, source_hw, target_hw):
    K = pinhole_matrix(K)
    if min(*source_hw, *target_hw) <= 0:
        raise ValueError('Pixel-grid sizes must be positive')
    K[0] *= float(target_hw[1]) / source_hw[1]
    K[1] *= float(target_hw[0]) / source_hw[0]
    return K


def rectification_maps(K, distortion, source_hw, target_hw, *, center=True):
    """OpenCV rational distortion, with an explicit output pixel matrix.

    R is identity: this changes rays/pixels, not the camera coordinate frame.
    A centred virtual camera also fits the MINT FoV-only training contract.
    """
    import cv2
    K = pinhole_matrix(K)
    d = np.asarray(distortion, np.float64).reshape(-1)
    if d.size not in (4, 5, 8, 12, 14) or not np.isfinite(d).all():
        raise ValueError('Declare finite OpenCV distortion coefficients')
    target = resize_intrinsics(K, source_hw, target_hw)
    if center:
        target[0, 2], target[1, 2] = target_hw[1] / 2., target_hw[0] / 2.
    maps = cv2.initUndistortRectifyMap(K, d, np.eye(3), target,
                                     (target_hw[1], target_hw[0]), cv2.CV_32FC1)
    return target, maps


def validate_pose_matrices(poses):
    value = np.asarray(poses, np.float64)
    if value.ndim != 3 or value.shape[1:] != (4, 4) or not np.isfinite(value).all():
        raise ValueError('Camera trajectory must contain finite [T,4,4] poses')
    R = value[:, :3, :3]
    if not np.allclose(value[:, 3], [0, 0, 0, 1], atol=1e-5) or not np.allclose(
            R.transpose(0, 2, 1) @ R, np.eye(3), atol=1e-4) or not np.allclose(np.linalg.det(R), 1, atol=1e-4):
        raise ValueError('Camera trajectory must contain proper rigid transforms')
    return value


def confidence_mask(joints, confidence=None):
    value = np.asarray(joints)
    valid = np.isfinite(value).all(axis=(-1, -2)) & (value[..., 2] > 0).all(axis=-1)
    if confidence is not None:
        conf = np.asarray(confidence)
        if conf.shape != value.shape[:-1] or not np.isfinite(conf).all() or np.any((conf < 0) | (conf > 1)):
            raise ValueError('Joint confidences must be finite probabilities [T,2,21]')
        valid &= (conf > 0).all(axis=-1)
    return valid


def project_points(points, K):
    value = np.asarray(points)
    K = pinhole_matrix(K)
    rays = np.einsum('ij,...j->...i', K, value)
    good = np.isfinite(value).all(axis=-1) & (value[..., 2] > 1e-6)
    pixels = np.divide(rays[..., :2], rays[..., 2:], out=np.full_like(rays[..., :2], np.nan),
                       where=good[..., None])
    return pixels, good
