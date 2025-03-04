###############################################################################
#   ilastik: interactive learning and segmentation toolkit
#
#       Copyright (C) 2011-2024, the ilastik developers
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
# 		   http://ilastik.org/license.html
###############################################################################
"""
Depending on the demand this might get reworked into a real "browser". Right now
this will only be used to punch in the url and do some validation. Naming of the
file is just to reflect the similar function as dvidDataSelectionBrowser.

Todos:
  - check whether can me somehow merged with dvidDataSelctionBrowser

"""

import logging
import pathlib

from requests.exceptions import SSLError, ConnectionError
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
)

from lazyflow.utility import isUrl
from lazyflow.utility.io_util.OMEZarrStore import OMEZarrStore
from lazyflow.utility.io_util.RESTfulPrecomputedChunkedVolume import RESTfulPrecomputedChunkedVolume
from lazyflow.utility.pathHelpers import uri_to_Path

logger = logging.getLogger(__name__)


def _validate_uri(text: str) -> str:
    """Make sure the input is a URI, convert if it's a path, ensure path exists if it's a 'file:' URI already.
    Returns a valid URI, or raises ValueError if invalid."""
    if text == "":
        raise ValueError('Please enter a path or URL, then press "Check".')
    if not isUrl(text):
        ospath = pathlib.Path(text)
        if ospath.exists():  # It's a local file path - convert to file: URI
            return ospath.as_uri()
        else:  # Maybe the user typed the address manually and forgot https://?
            raise ValueError('Please enter a URL including protocol ("http(s)://" or "file:").')
    elif isUrl(text) and text.startswith("file:"):
        # Check the file URI points to an existing path
        try:
            exists = uri_to_Path(text).exists()
        except ValueError:  # from uri_to_Path
            raise ValueError("Path is not absolute. Please try copy-pasting the full path.")
        if not exists:
            raise ValueError("Directory does not exist or URL is malformed. Please try copy-pasting the path directly.")
    return text


class MultiscaleDatasetBrowser(QDialog):

    EXAMPLE_URI = "https://data.ilastik.org/2d_cells_apoptotic_1channel.zarr"

    def __init__(self, history=None, parent=None):
        super().__init__(parent)
        self._history = history or []
        self.selected_uri = None  # Return value read by the caller after the dialog is closed

        self.setup_ui()

    def setup_ui(self):
        self.setMinimumSize(800, 200)
        self.setWindowTitle("Select Multiscale Source")
        main_layout = QVBoxLayout()

        description = QLabel(self)
        description.setText('Enter path or URL and click "Check".')
        main_layout.addWidget(description)

        self.combo = QComboBox(self)
        self.combo.setEditable(True)
        self.combo.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Minimum)

        for item in self._history:
            self.combo.addItem(item, item)

        self.combo.lineEdit().setPlaceholderText("Enter path/URL or choose from history...")
        self.combo.setCurrentIndex(-1)

        combo_label = QLabel(self)
        combo_label.setText("Dataset address: ")

        example_button = QPushButton(self)
        example_button.setText("Add example")
        example_button.setToolTip("Add url to '2d_cells_apoptotic_1channel` example from the ilastik website.")
        example_button.pressed.connect(lambda: self.combo.lineEdit().setText(self.EXAMPLE_URI))

        combo_layout = QGridLayout()
        chk_button = QPushButton(self)
        chk_button.setText("Check")
        chk_button.clicked.connect(self._validate_text_input)
        self.combo.lineEdit().returnPressed.connect(chk_button.click)
        combo_layout.addWidget(combo_label, 0, 0)
        combo_layout.addWidget(self.combo, 0, 1)
        combo_layout.addWidget(chk_button, 0, 2)
        combo_layout.addWidget(example_button, 1, 0)

        main_layout.addLayout(combo_layout)

        result_label = QLabel(self)
        result_label.setText("Metadata found at the given address: ")
        self.result_text_box = QTextBrowser(self)
        result_layout = QVBoxLayout()
        result_layout.addWidget(result_label)
        result_layout.addWidget(self.result_text_box)

        main_layout.addLayout(result_layout)

        self.qbuttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.qbuttons.accepted.connect(self.accept)
        self.qbuttons.rejected.connect(self.reject)
        self.qbuttons.button(QDialogButtonBox.Ok).setText("Add to project")
        self.qbuttons.button(QDialogButtonBox.Ok).setEnabled(False)

        def update_ok_button(current_entered_text):
            if current_entered_text == self.selected_uri:
                self.qbuttons.button(QDialogButtonBox.Ok).setEnabled(True)
            else:
                self.qbuttons.button(QDialogButtonBox.Ok).setEnabled(False)

        self.combo.lineEdit().textChanged.connect(update_ok_button)
        main_layout.addWidget(self.qbuttons)
        self.setLayout(main_layout)

    def _validate_text_input(self, _event):
        self.selected_uri = None
        text = self.combo.currentText().strip()
        try:
            uri = _validate_uri(text)
        except ValueError as e:
            self.result_text_box.setText(str(e))
            return
        if uri != text:
            self.combo.lineEdit().setText(uri)
        logger.debug(f"Entered URL: {uri}")
        try:
            # Ask each store type if it likes the URL to avoid web requests during instantiation attempts.
            if OMEZarrStore.is_uri_compatible(uri):
                rv = OMEZarrStore(uri)
            elif RESTfulPrecomputedChunkedVolume.is_uri_compatible(uri):
                rv = RESTfulPrecomputedChunkedVolume(volume_url=uri)
            else:
                store_types = [OMEZarrStore, RESTfulPrecomputedChunkedVolume]
                supported_formats = "\n".join(f"<li>{s.NAME} ({s.URI_HINT})</li>" for s in store_types)
                self.result_text_box.setHtml(
                    f"<p>Address does not look like any supported format.</p>"
                    f"<p>Supported formats:</p>"
                    f"<ul>{supported_formats}</ul>"
                )
                return
        except Exception as e:
            self.qbuttons.button(QDialogButtonBox.Ok).setEnabled(False)
            if isinstance(e, SSLError):
                msg = "SSL error, please check that you are using the correct protocol (http/https)."
            elif isinstance(e, ConnectionError):
                msg = "Connection error, please check that the server is online and the URL is correct."
            else:
                msg = "Couldn't load a multiscale dataset at this address."
            msg += f"\n\nMore detail:\n{e}"
            logger.error(e, exc_info=True)
            self.result_text_box.setText(msg)
            return

        self.selected_uri = uri
        scale_info_text = "\n".join(
            [f"  - {key}: {' / '.join(map(str, shape.values()))}" for key, shape in rv.multiscales.items()]
        )
        self.result_text_box.setText(
            f"URL: {self.selected_uri}\nData format: {rv.NAME}\nAvailable scales:\n" + scale_info_text
        )
        # This check-button might have been triggered by pressing Enter.
        # The timer prevents triggering the now enabled OK button by the same keypress.
        QTimer.singleShot(0, lambda: self.qbuttons.button(QDialogButtonBox.Ok).setEnabled(True))


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication

    app = QApplication([])

    logging.basicConfig(level=logging.INFO)

    pv = MultiscaleDatasetBrowser()
    pv.combo.addItem("test")
    pv.show()
    app.exec_()
    print(pv.result(), pv.selected_uri)
