"""Force-delete confirmation dialog - shows the exact kill list an
analyze() call computed (never recomputed here), gated behind a required
"이해했습니다" checkbox before the Execute button becomes clickable. This
is the one and only place in the app a destructive force-delete can be
confirmed from, regardless of which page initiated it.

Assumes result.blocked is already False - the caller is expected to have
checked that and shown its own error instead of opening this dialog at
all for a blocked path (see core/force_delete.py's AnalyzeResult).
"""
from __future__ import annotations

from typing import List

from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QVBoxLayout,
)

from core.force_delete import AnalyzeResult, ExecuteOptions
from core.formatting import format_bytes


class ConfirmForceDeleteDialog(QDialog):
    def __init__(self, result: AnalyzeResult, parent=None):
        super().__init__(parent)
        self._result = result
        self.setWindowTitle("강제 삭제 확인")
        self.resize(480, 420)

        layout = QVBoxLayout(self)

        summary = QLabel(f"경로: {result.path}\n파일 {result.file_count}개, {format_bytes(result.total_bytes)}")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        if result.locking_processes:
            layout.addWidget(
                QLabel(f"다음 {len(result.locking_processes)}개 프로세스가 이 경로를 사용 중이며, 종료됩니다:")
            )
            proc_list = QListWidget()
            for proc in result.locking_processes:
                proc_list.addItem(f"{proc.name} (PID {proc.pid})")
            layout.addWidget(proc_list)
        else:
            layout.addWidget(QLabel("이 경로를 사용 중인 프로세스가 없습니다."))

        if not result.is_admin:
            layout.addWidget(QLabel("⚠ 관리자 권한이 아닙니다. 일부 파일은 삭제하지 못할 수 있습니다."))

        self._shred_checkbox = QCheckBox("보안 삭제 (덮어쓰기 후 삭제) - HDD에는 효과적이나 SSD에는 효과가 제한적입니다.")
        layout.addWidget(self._shred_checkbox)

        self._confirm_checkbox = QCheckBox("위 내용을 이해했으며, 계속 진행합니다.")
        layout.addWidget(self._confirm_checkbox)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._ok_button = buttons.button(QDialogButtonBox.Ok)
        self._ok_button.setEnabled(False)
        self._ok_button.setText("삭제 실행")
        self._confirm_checkbox.toggled.connect(self._ok_button.setEnabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def build_options(self) -> ExecuteOptions:
        return ExecuteOptions(
            kill_locking_processes=True,
            take_ownership_on_failure=True,
            secure_shred=self._shred_checkbox.isChecked(),
            reboot_delete_fallback=True,
        )

    @property
    def kill_pids(self) -> List[int]:
        return [p.pid for p in self._result.locking_processes]
