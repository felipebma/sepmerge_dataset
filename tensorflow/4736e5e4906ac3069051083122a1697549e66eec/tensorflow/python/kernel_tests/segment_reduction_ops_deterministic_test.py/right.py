# Copyright 2021 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Tests for deterministic functionality of segment reduction ops."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os

from tensorflow.python.eager import backprop
from tensorflow.python.framework import constant_op
from tensorflow.python.framework import dtypes
from tensorflow.python.framework import errors_impl
from tensorflow.python.framework import indexed_slices
from tensorflow.python.framework import ops
from tensorflow.python.framework import test_util
from tensorflow.python.ops import array_ops
from tensorflow.python.ops import math_ops
from tensorflow.python.ops import variables
from tensorflow.python.platform import test


class SegmentReductionDeterminismExceptionsTest(test.TestCase):
  """Test d9m-unimplemented exceptions from the segment reduction ops.

  Test that tf.errors.UnimplementedError is thrown or not thrown, as
  appropriate, by the GPU code-paths for segment reduction ops when
  deterministic ops are enabled.

  This test assumes that the base op test runs all the same test cases when
  deterministic ops are not enabled and will therefore detect erroneous
  exception throwing in those cases.
  """

  def _input(self, data_type, segment_ids_type):
    data = constant_op.constant([[1, 2, 3, 4], [5, 6, 7, 8]], dtype=data_type)
    segment_ids = constant_op.constant([0, 1], dtype=segment_ids_type)
    num_segments = 2
    return data, segment_ids, num_segments

  @test_util.run_cuda_only
  def testSortedOps(self):
    op_should_throw_for_float = {
        math_ops.segment_max: False,
        math_ops.segment_min: False,
        math_ops.segment_mean: False,  # implemented on CPU only
        math_ops.segment_prod: True,
        math_ops.segment_sum: True,
    }
    for op, should_throw_for_float in op_should_throw_for_float.items():
      for segment_ids_type in [dtypes.int32, dtypes.int64]:
        for data_type in [dtypes.float16, dtypes.float32, dtypes.float64]:
          with self.cached_session(force_gpu=True):
            data, segment_ids, _ = self._input(data_type, segment_ids_type)
            if should_throw_for_float:
              with self.assertRaisesRegex(
                  errors_impl.UnimplementedError,
                  "Deterministic GPU implementation of sorted segment " +
                  "reduction op not available."):
                op(data, segment_ids)
            else:
              op(data, segment_ids)

  _UNSORTED_ERROR_MESSAGE = ("Deterministic GPU implementation of unsorted " +
                             "segment reduction op not available.")

  @test_util.run_cuda_only
  @test_util.run_in_graph_and_eager_modes
  def testUnsortedOps(self):
    op_should_throw_for_float = {
        math_ops.unsorted_segment_max: False,
        math_ops.unsorted_segment_min: False,
        math_ops.unsorted_segment_mean: True,  # uses unsorted_segment_sum
        math_ops.unsorted_segment_sqrt_n: True,  # uses unsorted_segment_sum
        math_ops.unsorted_segment_prod: True,
        math_ops.unsorted_segment_sum: True,
    }
    with self.session(force_gpu=True):
      for op, should_throw_for_float in op_should_throw_for_float.items():
        for segment_ids_type in [dtypes.int32, dtypes.int64]:
          for data_type in [
              dtypes.float16, dtypes.float32, dtypes.float64, dtypes.int32
          ]:
            if (op == math_ops.unsorted_segment_sqrt_n and
                data_type == dtypes.int32):  # sqrt_n doesn't support int32
              continue
            data, segment_ids, num_segments = self._input(
                data_type, segment_ids_type)
            if (data_type != dtypes.int32) and should_throw_for_float:
              with self.assertRaisesRegex(errors_impl.UnimplementedError,
                                          self._UNSORTED_ERROR_MESSAGE):
                result = op(data, segment_ids, num_segments)
                self.evaluate(result)
            else:
              result = op(data, segment_ids, num_segments)
              self.evaluate(result)

  @test_util.run_cuda_only
  def testUnsortedOpsComplex(self):
    for op in [
        math_ops.unsorted_segment_mean,  # uses unsorted_segment_sum
        math_ops.unsorted_segment_sqrt_n,  # uses unsorted_segment_sum
        math_ops.unsorted_segment_sum,
    ]:
      for data_type in [dtypes.complex64, dtypes.complex128]:
        for segment_ids_type in [dtypes.int32, dtypes.int64]:
          with self.cached_session(force_gpu=True):
            data, segment_ids, num_segments = self._input(
                data_type, segment_ids_type)
            with self.assertRaisesRegex(errors_impl.UnimplementedError,
                                        self._UNSORTED_ERROR_MESSAGE):
              op(data, segment_ids, num_segments)

  @test_util.run_cuda_only
  @test_util.run_in_graph_and_eager_modes
  def testConvertToTensor(self):
    with self.session(force_gpu=True):
      for data_type in [
          dtypes.float16, dtypes.float32, dtypes.float64, dtypes.complex64,
          dtypes.complex128
      ]:
        for segment_ids_type in [dtypes.int32, dtypes.int64]:
          values, indices, _ = self._input(data_type, segment_ids_type)
          sparse_value = indexed_slices.IndexedSlices(
              values, indices, dense_shape=values.shape)
          with self.assertRaisesRegex(errors_impl.UnimplementedError,
                                      self._UNSORTED_ERROR_MESSAGE):
            # convert_to_tensor with IndexedSlices uses unsorted_segment_sum
            result = ops.convert_to_tensor(sparse_value)
            self.evaluate(result)

  @test_util.run_cuda_only
  def testGatherBackprop(self):
    for data_type in [
        dtypes.float16, dtypes.float32, dtypes.float64, dtypes.complex64,
        dtypes.complex128
    ]:
      for segment_ids_type in [dtypes.int32, dtypes.int64]:
        with self.cached_session(force_gpu=True):
          params, indices, _ = self._input(dtypes.float32, dtypes.int32)
          params = variables.Variable(params)
          with backprop.GradientTape() as tape:
            tape.watch(params)
            op_output = array_ops.gather(params, indices)
          gradient = tape.gradient(op_output, params)
          with self.assertRaisesRegex(errors_impl.UnimplementedError,
                                      self._UNSORTED_ERROR_MESSAGE):
            params.assign(gradient)  # convert_to_tensor on IndexedSlices


if __name__ == "__main__":
  # Note that the effect of setting the following environment variable to
  # 'true' is not tested. Unless we can find a simpler pattern for testing these
  # environment variables, it would require this file to be made into a base
  # and then two more test files to be created.
  os.environ["TF_DETERMINISTIC_OPS"] = "1"
  test.main()
