###############################################################################
#   lazyflow: data flow based lazy parallel computation framework
#
#       Copyright (C) 2011-2024, the ilastik developers
#                                <team@ilastik.org>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the Lesser GNU General Public License
# as published by the Free Software Foundation; either version 2.1
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# See the files LICENSE.lgpl2 and LICENSE.lgpl3 for full text of the
# GNU Lesser General Public License version 2.1 and 3 respectively.
# This information is also available on the ilastik web site at:
#          http://ilastik.org/license/
###############################################################################
import logging

from lazyflow.graph import Operator, InputSlot, OutputSlot
from lazyflow.utility.io_util.OMEZarrStore import OMEZarrStore

logger = logging.getLogger(__name__)


class OpOMEZarrMultiscaleReader(Operator):
    """
    Operator to plug the OME-Zarr loader into lazyflow.

    :param metadata_only_mode: Passed through to the internal OMEZarrStore.
        If True, only the last scale is loaded to determine the dtype. Used to shorten init time
        when DatasetInfo instantiates an OpInputDataReader to get lane shape and dtype.
    """

    name = "OpOMEZarrMultiscaleReader"

    BaseUri = InputSlot()
    Scale = InputSlot(optional=True)

    Output = OutputSlot()

    def __init__(self, metadata_only_mode=False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._load_only_one_scale = metadata_only_mode
        self._store = None

    def setupOutputs(self):
        if self._store is not None and self._store.uri == self.BaseUri.value:
            # Must not set Output.meta here.
            return

        self._store = OMEZarrStore(self.BaseUri.value, self._load_only_one_scale)
        active_scale = self.Scale.value if self.Scale.ready() else self._store.lowest_resolution_key
        self.Output.meta.shape = self._store.get_shape(active_scale)
        self.Output.meta.dtype = self._store.dtype
        self.Output.meta.axistags = self._store.axistags
        self.Output.meta.scales = self._store.multiscales
        self.Output.meta.active_scale = active_scale  # Used by export to correlate export with input scale
        # To feed back to DatasetInfo and hence the project file
        self.Output.meta.lowest_scale = self._store.lowest_resolution_key
        # Many public OME-Zarr datasets are chunked as full xy slices,
        # so orthoviews lead to downloading the entire dataset.
        self.Output.meta.prefer_2d = True
        # Add OME-Zarr metadata to slot so that it can be ported over to an export
        self.Output.meta.ome_zarr_meta = self._store.ome_meta_for_export

    def execute(self, slot, subindex, roi, result):
        scale = self.Scale.value if self.Scale.ready() and self.Scale.value else self._store.lowest_resolution_key
        result[...] = self._store.request(roi, scale)
        return result

    def propagateDirty(self, slot, subindex, roi):
        self.Output.setDirty(slice(None))
