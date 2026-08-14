"""'View Details' Quick Action - a sortable snapshot table of every
process's PID, name, RAM, and CPU% at the moment it's opened (not live-
updating; this is a one-shot detail view, not a second dashboard).
"""
from __future__ import annotations

from PyQt5.QtWidgets import QDialog, QTableWidget, QTableWidgetItem, QVBoxLayout

from core.formatting import format_bytes
from core.metrics import top_processes


class MemoryDetailsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("프로세스 상세 정보")
        self.resize(560, 480)

        layout = QVBoxLayout(self)
        table = QTableWidget(self)
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["이름", "PID", "메모리", "CPU %"])
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSortingEnabled(True)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)

        processes = top_processes(limit=200)
        table.setRowCount(len(processes))
        for row, proc in enumerate(processes):
            table.setItem(row, 0, QTableWidgetItem(proc.name))
            table.setItem(row, 1, QTableWidgetItem(str(proc.pid)))
            table.setItem(row, 2, QTableWidgetItem(format_bytes(proc.memory_bytes)))
            table.setItem(row, 3, QTableWidgetItem(f"{proc.cpu_percent:.1f}"))
