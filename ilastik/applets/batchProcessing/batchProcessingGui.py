###############################################################################
#   ilastik: interactive learning and segmentation toolkit
#
#       Copyright (C) 2011-2014, the ilastik developers
#                                <team@ilastik.org>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# In addition, as a special exception, the copyright holders of
# ilastik give you permission to combine ilastik with applets,
# workflows and plugins which are not covered under the GNU
# General Public License.
#
# See the LICENSE file for details. License information is also available
# on the ilastik web site at:
#           http://ilastik.org/license.html
###############################################################################

import logging
import os
import typing
import webbrowser
from collections import OrderedDict
from functools import partial

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import ilastik.config
from ilastik.applets.batchProcessing import hbp
from ilastik.utility import log_exception
from ilastik.utility.gui import ThreadRouter, threadRouted
from ilastik.widgets.ImageFileDialog import ImageFileDialog
from lazyflow.request import Request

logger = logging.getLogger(__name__)


class BatchProcessingDataConstraintException(Exception):
    pass


class FileListWidget(QListWidget):
    """QListWidget with custom drag-n-drop for file paths
    """

    def dropEvent(self, dropEvent):
        urls = dropEvent.mimeData().urls()
        self.clear()
        self.addItems(qurl.path() for qurl in urls)

    def dragEnterEvent(self, event):
        # Only accept drag-and-drop events that consist of urls to local files.
        if not event.mimeData().hasUrls():
            return
        urls = event.mimeData().urls()
        if all(url.isLocalFile() for url in urls):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        # Must override this or else the QTableView base class steals dropEvents from us.
        pass


class BatchRoleWidget(QWidget):
    """Container Widget for Batch File list and buttons
    """

    def __init__(self, role_name: str, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._role_name = role_name
        self._init_ui()

    def _init_ui(self):
        self.select_button = QPushButton(f"Select {self._role_name} Files...")
        self.clear_button = QPushButton(f"Clear {self._role_name} Files")
        button_layout = QHBoxLayout()
        button_layout.addWidget(self.select_button)
        button_layout.addSpacerItem(QSpacerItem(0, 0, hPolicy=QSizePolicy.Expanding))
        button_layout.addWidget(self.clear_button)
        button_layout.setContentsMargins(0, 0, 0, 0)

        self.list_widget = FileListWidget(parent=self)
        self.list_widget.setSizePolicy(QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding))
        self.list_widget.setAcceptDrops(True)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addLayout(button_layout)
        main_layout.addWidget(self.list_widget)

        self.clear_button.clicked.connect(self.clear)
        self.select_button.clicked.connect(self.select_files)

        self.setLayout(main_layout)

    @property
    def filepaths(self) -> typing.List[str]:
        """
        Utility function.
        Return all items in the given QListWidget as a list of strings.
        """
        all_item_strings = []
        for row in range(self.list_widget.count()):
            all_item_strings.append(self.list_widget.item(row).text())
        return all_item_strings

    def select_files(self):
        preference_name = f"recent-dir-role-{self._role_name}"
        file_paths = ImageFileDialog(
            self, preferences_group="BatchProcessing", preferences_setting=preference_name
        ).getSelectedPaths()
        if file_paths:
            self.clear()
            self.list_widget.addItems(map(str, file_paths))

    def clear(self):
        """Remove all items from the list"""
        self.list_widget.clear()


class BatchProcessingGui(QTabWidget):
    """
    """

    ###########################################
    ### AppletGuiInterface Concrete Methods ###
    ###########################################

    def centralWidget(self):
        return self

    def appletDrawer(self):
        return self._drawer

    def menus(self):
        return []

    def viewerControlWidget(self):
        return QWidget(parent=self)  # No viewer, so no viewer controls.

    # This applet doesn't care what image is selected in the interactive flow
    def setImageIndex(self, index):
        pass

    def imageLaneAdded(self, laneIndex):
        pass

    def imageLaneRemoved(self, laneIndex, finalLength):
        pass

    def allowLaneSelectionChange(self):
        return False

    def stopAndCleanUp(self):
        # We don't have any complex things to clean up (e.g. no layer viewers)
        pass

    ###########################################
    ###########################################

    def __init__(self, parentApplet):
        super().__init__()
        self.parentApplet = parentApplet
        self.threadRouter = ThreadRouter(self)
        self._drawer = None
        self._data_role_widgets = {}
        self.initMainUi()
        self.initAppletDrawerUi()
        self.export_req = None

    def initMainUi(self):

        role_names = self.parentApplet.dataSelectionApplet.topLevelOperator.DatasetRoles.value
        # Create a tab for each role
        for role_name in role_names:
            assert role_name not in self._data_role_widgets
            data_role_widget = BatchRoleWidget(role_name=role_name, parent=self)
            self.addTab(data_role_widget, role_name)
            self._data_role_widgets[role_name] = data_role_widget

    def initAppletDrawerUi(self):
        instructions_label = QLabel(
            "Select the input files for batch processing "
            "using the controls on the right.\n"
            "The results will be exported according "
            "to the same settings you chose in the "
            "interactive export page above."
        )
        instructions_label.setWordWrap(True)
        instructions_label.setAlignment(Qt.AlignCenter)
        self.run_button = QPushButton("Process all files", clicked=self.run_export)
        self.cancel_button = QPushButton("Cancel processing", clicked=self.cancel_batch_processing)
        self.cancel_button.setVisible(False)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(instructions_label)
        layout.addWidget(self.run_button)
        layout.addWidget(self.cancel_button)

        if ilastik.config.cfg["ilastik"].getboolean("hbp") and self._hbp_category:
            layout.addWidget(QPushButton("Upload Project File to HBP", self, clicked=self._hbp_upload_project_file))

        self._drawer = QWidget(parent=self)
        self._drawer.setLayout(layout)

    @property
    def _hbp_category(self):
        return hbp.category(self.parentApplet.workflow())

    def _hbp_upload_project_file(self):
        webbrowser.open(ilastik.config.cfg["hbp"]["token_url"])
        token, ok = QInputDialog.getText(self, "Client Token", "Paste your token from a browser window")
        if not ok:
            return

        workflow = self.parentApplet.workflow()
        opDataSelection = workflow.dataSelectionApplet.topLevelOperator
        opFeatureSelection = workflow.featureSelectionApplet.topLevelOperator

        workflow.shell.projectManager.saveProject()  # Ensure that project_file is in sync.
        project_file = opDataSelection.ProjectFile.value

        payload = hbp.Payload(
            token=token,
            project=hbp.serialize_project_file(project_file),
            filename=os.path.basename(project_file.filename),
            num_channels=hbp.num_channels(opDataSelection),
            min_block_size=hbp.min_block_size(opFeatureSelection),
            compute_in_2d=hbp.compute_in_2d(opFeatureSelection),
            workflow=self._hbp_category,
        )

        try:
            webpage_url = hbp.send_payload(payload)
            webbrowser.open(webpage_url)
        except Exception as e:
            QMessageBox.critical(self, "Exception", f"<pre>{e}</pre>")

    def run_export(self):
        role_names = self.parentApplet.dataSelectionApplet.topLevelOperator.DatasetRoles.value

        # Prepare file lists in an OrderedDict
        role_path_dict = OrderedDict(
            (role_name, self._data_role_widgets[role_name].filepaths) for role_name in role_names
        )
        dominant_role_name = role_names[0]
        num_paths = len(role_path_dict[dominant_role_name])

        if num_paths == 0:
            return

        for role_name in role_names[1:]:
            paths = role_path_dict[role_name]
            if len(paths) == 0:
                role_path_dict[role_name] = [None] * num_paths

            if len(role_path_dict[role_name]) != num_paths:
                raise BatchProcessingDataConstraintException(
                    f"Number of files for '{role_name!r}' does not match! " f"Exptected {num_paths} files."
                )

        # Run the export in a separate thread
        export_req = Request(partial(self.parentApplet.run_export, role_path_dict))
        export_req.notify_failed(self.handle_batch_processing_failure)
        export_req.notify_finished(self.handle_batch_processing_finished)
        export_req.notify_cancelled(self.handle_batch_processing_cancelled)
        self.export_req = export_req

        self.parentApplet.busy = True
        self.parentApplet.appletStateUpdateRequested()
        self.cancel_button.setVisible(True)
        self.run_button.setEnabled(False)

        # Start the export
        export_req.submit()

    def handle_batch_processing_complete(self):
        """
        Called after batch processing completes, no matter how it finished (failed, cancelled, whatever).
        Can be overridden in subclasses.
        """
        pass

    def cancel_batch_processing(self):
        assert self.export_req, "No export is running, how were you able to press 'cancel'?"
        self.export_req.cancel()

    @threadRouted
    def handle_batch_processing_finished(self, *args):
        self.parentApplet.busy = False
        self.parentApplet.appletStateUpdateRequested()
        self.export_req = None
        self.cancel_button.setVisible(False)
        self.run_button.setEnabled(True)
        self.handle_batch_processing_complete()

    @threadRouted
    def handle_batch_processing_failure(self, exc, exc_info):
        msg = "Error encountered during batch processing:\n{}".format(exc)
        log_exception(logger, msg, exc_info)
        self.handle_batch_processing_finished()
        self.handle_batch_processing_complete()
        QMessageBox.critical(self, "Batch Processing Error", msg)

    @threadRouted
    def handle_batch_processing_cancelled(self):
        self.handle_batch_processing_finished()
        self.handle_batch_processing_complete()
        QMessageBox.information(self, "Batch Processing Cancelled.", "Batch Processing Cancelled.")
