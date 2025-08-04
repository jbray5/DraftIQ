#gui_player_display.py

import sys
import os
import json
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLabel, QScrollArea,
    QGridLayout, QTableWidget, QTableWidgetItem, QPushButton, QApplication, 
    QFrame
)
from PyQt5.QtGui import QDrag
from PyQt5.QtCore import Qt, QMimeData
import pandas as pd


class DraggablePlayerLabel(QLabel):
    def __init__(self, player_name, player_id, position, app_ref, injury_score):
        super().__init__(player_name)
        self.player_name = player_name
        self.player_id = player_id
        self.position = position
        self.injury_score = injury_score
        self.assigned = False
        self.app_ref = app_ref
        self.setStyleSheet(
            """
            QLabel {
                border: 1px solid #444;
                padding: 12px;
                background-color: #222;
                color: #ddd;
                border-radius: 5px;
                font-family: Arial;
                font-size: 18px;
            }
            """
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.assigned:
            drag = QDrag(self)
            mime_data = QMimeData()
            mime_data.setText(f"{self.player_name},{self.player_id},{self.position},{self.injury_score}")
            drag.setMimeData(mime_data)
            drag.exec_(Qt.MoveAction)

    def mark_as_assigned(self):
        self.assigned = True
        self.setStyleSheet(
            """
            QLabel {
                color: #999;
                text-decoration: line-through;
                border: 1px solid #555;
                background-color: #333;
                border-radius: 5px;
                font-family: Arial;
                font-size: 18px;
            }
            """
        )
        self.setEnabled(False)

    def mark_as_available(self):
        self.assigned = False
        self.setStyleSheet(
            """
            QLabel {
                border: 1px solid #444;
                padding: 12px;
                background-color: #222;
                color: #ddd;
                border-radius: 5px;
                font-family: Arial;
                font-size: 18px;
            }
            """
        )
        self.setEnabled(True)


class RosterSlotLabel(QLabel):
    def __init__(self, slot_name, allowed_positions, app_ref=None, parent=None):
        super().__init__(slot_name, parent)
        self.slot_name = slot_name
        self.allowed_positions = allowed_positions
        self.player_label = None
        self.app_ref = app_ref
        self.setAcceptDrops(True)
        self.setFrameStyle(QFrame.Box | QFrame.Plain)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(250, 60)
        self.setStyleSheet(
            """
            QLabel {
                background-color: #2a2a2a;
                color: #ff4444;
                border: 1px solid #444;
                border-radius: 8px;
                font-family: Arial;
                font-size: 18px;
            }
            """
        )

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            player_name, player_id, position, injury_score = event.mimeData().text().split(',')
            if position in self.allowed_positions:
                event.acceptProposedAction()

    def dropEvent(self, event):
        player_data = event.mimeData().text()
        player_name, player_id, position, injury_score = player_data.split(',')

        if position in self.allowed_positions:
            if self.player_label:
                self.player_label.mark_as_available()

            clean_name = player_name.split('(')[0].strip()
            self.setText(clean_name)

            self.setStyleSheet(
                """
                QLabel {
                    background-color: #444;
                    color: #fff;
                    border: 1px solid #666;
                    border-radius: 8px;
                    font-family: Arial;
                    font-size: 18px;
                }
                """
            )

            for label in self.app_ref.player_labels:
                if label.player_id == int(player_id):
                    label.mark_as_assigned()
                    self.player_label = label

            self.app_ref.update_best_pick_label()
            self.app_ref.update_pick_order()  # Automatically update the draft pick after a selection
            event.acceptProposedAction()

    def mouseDoubleClickEvent(self, event):
        if self.player_label:
            self.player_label.mark_as_available()
            self.clear_slot()
            self.app_ref.update_best_pick_label()

    def clear_slot(self):
        self.setText(self.slot_name)
        self.setStyleSheet(
            """
            QLabel {
                background-color: #2a2a2a;
                color: #ff4444;
                border: 1px solid #444;
                border-radius: 8px;
                font-family: Arial;
                font-size: 18px;
            }
            """
        )
        self.player_label = None


class PlayerDisplayApp(QWidget):
    SAVE_FILE = "draft_progress.json"

    def __init__(self, ranked_players_path):
        super().__init__()
        self.ranked_players = pd.read_csv(ranked_players_path)
        self.ranked_players = self.ranked_players.sort_values(by='Composite_Score', ascending=False)
        self.draft_order = [
            "Wester", "Spivey", "D-Put", "Walker", "Will", 
            "Thom", "JRay", "Taylor", "Mac", "Cuda",
            "CJ", "Josh"
        ]
        self.current_pick_index = 0
        self.snake_direction = 1
        self.player_labels = []
        self.roster_labels = {}
        self.setup_ui()
        self.load_progress()
        self.update_best_pick_label()

    def setup_ui(self):
        self.setWindowTitle(f"Draft Tool - Current Pick: {self.draft_order[self.current_pick_index]}")
        self.setGeometry(100, 100, 1600, 900)
        self.setStyleSheet("QWidget { background-color: #1a1a1a; }")

        self.main_layout = QHBoxLayout()
        self.setLayout(self.main_layout)
        self.left_layout = QVBoxLayout()

        self.position_filter_dropdown = QComboBox(self)
        self.position_filter_dropdown.addItems(["All", "QB", "RB", "WR", "TE", "DST", "K"])
        self.position_filter_dropdown.currentIndexChanged.connect(self.update_player_list)
        self.position_filter_dropdown.setStyleSheet("""QComboBox {
            border: 1px solid #555; padding: 10px; background-color: #333; color: #fff;
            font-family: Arial; font-size: 18px; border-radius: 8px; }
            QComboBox::drop-down { border: 0px; }
            QComboBox QAbstractItemView {
                background-color: #333; selection-background-color: #555;
                color: #fff; font-size: 18px;
            }""")
        self.left_layout.addWidget(self.position_filter_dropdown)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_content = QWidget(self.scroll)
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll.setWidget(self.scroll_content)
        self.left_layout.addWidget(self.scroll)
        self.main_layout.addLayout(self.left_layout)

        self.right_scroll = QScrollArea()
        self.right_scroll.setWidgetResizable(True)
        self.right_content = QWidget()
        self.right_layout = QGridLayout(self.right_content)
        self.right_scroll.setWidget(self.right_content)
        self.main_layout.addWidget(self.right_scroll)

        roster_slots = {
            "QB": ["QB"], "RB1": ["RB"], "RB2": ["RB"], "WR1": ["WR"], "WR2": ["WR"], "TE": ["TE"],
            "FLEX": ["RB", "WR", "TE"], "DST": ["DST"], "K": ["K"],
            "Bench1": ["QB", "RB", "WR", "TE", "DST", "K"],
            "Bench2": ["QB", "RB", "WR", "TE", "DST", "K"],
            "Bench3": ["QB", "RB", "WR", "TE", "DST", "K"],
            "Bench4": ["QB", "RB", "WR", "TE", "DST", "K"],
            "Bench5": ["QB", "RB", "WR", "TE", "DST", "K"],
            "Bench6": ["QB", "RB", "WR", "TE", "DST", "K"],
            "IDP": ["DE", "ILB", "CB", "S"]
        }

        num_columns = 2
        for i, team_name in enumerate(self.draft_order):
            team_label = QLabel(f"{team_name} Roster:")
            team_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #ff4444;")
            self.right_layout.addWidget(team_label, i // num_columns * 17, (i % num_columns) * 2)

            for j, slot in enumerate(list(roster_slots.keys())[:15]):
                from gui_player_display import RosterSlotLabel
                allowed_positions = roster_slots[slot]
                roster_slot = RosterSlotLabel(slot, allowed_positions, app_ref=self)
                self.right_layout.addWidget(roster_slot, (i // num_columns) * 17 + j + 1, (i % num_columns) * 2 + 1)
                self.roster_labels[f"{team_name}_{slot}"] = roster_slot

        self.best_pick_label = QLabel("Loading best pick...")
        self.best_pick_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #44ff44;")
        self.left_layout.addWidget(self.best_pick_label)

        self.recommendation_table = QTableWidget()
        self.recommendation_table.setColumnCount(8)
        self.recommendation_table.setHorizontalHeaderLabels([
            "Player", "Pos", "Score", "VORP", "ADP", "Ceiling", "Opp", "Injury"
        ])
        self.recommendation_table.setStyleSheet("""
            QHeaderView::section {
                background-color: #444;
                color: #fff;
                padding: 4px;
                font-size: 14px;
                font-weight: bold;
            }
            QTableWidget {
                background-color: #222;
                color: #eee;
                gridline-color: #555;
                font-size: 14px;
                alternate-background-color: #2a2a2a;
            }
            QTableWidget::item:selected {
                background-color: #555;
                color: #fff;
            }
        """)
        self.recommendation_table.setAlternatingRowColors(True)
        self.recommendation_table.setShowGrid(True)
        self.recommendation_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.recommendation_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.left_layout.addWidget(self.recommendation_table)

        self.reset_button = QPushButton("Reset Draft")
        self.reset_button.clicked.connect(self.reset_draft)
        self.left_layout.addWidget(self.reset_button)

        # Save draft button
        self.save_button = QPushButton("Save Draft")
        self.save_button.clicked.connect(self.save_progress)
        self.left_layout.addWidget(self.save_button)

        # Load draft button
        self.load_button = QPushButton("Load Draft")
        self.load_button.clicked.connect(self.on_manual_load)
        self.left_layout.addWidget(self.load_button)

        self.position_filter_dropdown.setCurrentIndex(0)
        self.update_player_list()

    def update_player_list(self):
        selected_position = self.position_filter_dropdown.currentText()
        self.player_labels.clear()

        for i in reversed(range(self.scroll_layout.count())):
            widget = self.scroll_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        filtered_players = self.ranked_players
        if selected_position != "All":
            filtered_players = filtered_players[filtered_players['Position_stats'] == selected_position]

        from gui_player_display import DraggablePlayerLabel
        for _, player in filtered_players.iterrows():
            injury_score = player.get('Injury_Score', 0)
            label = DraggablePlayerLabel(
                f"{player['Name_stats']} ({player['Position_stats']}) - ADP: {player['AverageDraftPositionPPR_proj']} - VORP: {player['VORP']:.2f} - Composite: {player['Composite_Score']:.2f} - Injury: {injury_score}",
                player['PlayerID'],
                player['Position_stats'],
                app_ref=self,
                injury_score=injury_score
            )
            if self.player_assigned_to_roster(player['PlayerID']):
                label.mark_as_assigned()
            self.player_labels.append(label)
            self.scroll_layout.addWidget(label)
        self.scroll_content.setLayout(self.scroll_layout)

    def player_assigned_to_roster(self, player_id):
        return any(
            slot.player_label and slot.player_label.player_id == player_id
            for slot in self.roster_labels.values()
        )

    def update_pick_order(self):
        self.current_pick_index += self.snake_direction
        if self.current_pick_index >= len(self.draft_order):
            self.current_pick_index = len(self.draft_order) - 1
            self.snake_direction = -1
        elif self.current_pick_index < 0:
            self.current_pick_index = 0
            self.snake_direction = 1
        self.setWindowTitle(f"Draft Tool - Current Pick: {self.draft_order[self.current_pick_index]}")
        self.update_best_pick_label()

    def predict_next_pick(self):
        non_idp = ['QB', 'RB', 'WR', 'TE', 'K', 'DST']
        needs = {'QB': 1, 'RB': 2, 'WR': 2, 'TE': 1, 'FLEX': 1, 'DST': 1, 'K': 1}
        team = self.draft_order[self.current_pick_index]
        filled = self.get_filled_roster_slots(team)
        to_fill = {pos: cnt for pos, cnt in needs.items() if filled[pos] < cnt}

        best_score = float('-inf')
        best_player = None

        for _, player in self.ranked_players.iterrows():
            pos = player['Position_stats']
            if pos not in non_idp or self.player_assigned_to_roster(player['PlayerID']):
                continue
            score = player['Composite_Score']
            if to_fill.get(pos, 0) > 0 or (pos in ['RB', 'WR', 'TE'] and to_fill.get('FLEX', 0) > 0):
                wscore = score
            else:
                wscore = score * 0.5
            if wscore > best_score:
                best_score = wscore
                best_player = player

        return (f"Next Best Pick for {team}: {best_player['Name_stats']} "
                f"({best_player['Position_stats']}) - Composite: {best_score:.2f}"
                if best_player is not None else "No suitable players available for the next pick.")

    def get_filled_roster_slots(self, team):
        filled = {k: 0 for k in ['QB', 'RB', 'WR', 'TE', 'FLEX', 'DST', 'K']}
        for slot_key, slot in self.roster_labels.items():
            if team in slot_key and slot.player_label:
                pos = slot.slot_name
                if pos.startswith("RB"):
                    filled['RB'] += 1
                elif pos.startswith("WR"):
                    filled['WR'] += 1
                else:
                    filled[pos] += 1
        return filled

    def update_best_pick_label(self):
        self.best_pick_label.setText(self.predict_next_pick())
        self.update_recommendation_table()

    def update_recommendation_table(self):
        non_idp = ['QB', 'RB', 'WR', 'TE', 'K', 'DST']
        team = self.draft_order[self.current_pick_index]
        filled = self.get_filled_roster_slots(team)
        needs = {'QB': 1, 'RB': 2, 'WR': 2, 'TE': 1, 'FLEX': 1, 'DST': 1, 'K': 1}
        to_fill = {pos: cnt for pos, cnt in needs.items() if filled[pos] < cnt}
        candidates = []

        for _, player in self.ranked_players.iterrows():
            if player['Position_stats'] not in non_idp or self.player_assigned_to_roster(player['PlayerID']):
                continue
            w = 1 if to_fill.get(player['Position_stats'], 0) > 0 or (
                player['Position_stats'] in ['RB', 'WR', 'TE'] and to_fill.get('FLEX', 0) > 0
            ) else 0.5
            candidates.append((player['Composite_Score'] * w, player))

        top5 = sorted(candidates, key=lambda x: x[0], reverse=True)[:5]
        self.recommendation_table.setRowCount(len(top5))
        for i, (_, p) in enumerate(top5):
            self.recommendation_table.setItem(i, 0, QTableWidgetItem(str(p['Name_stats'])))
            self.recommendation_table.setItem(i, 1, QTableWidgetItem(str(p['Position_stats'])))
            self.recommendation_table.setItem(i, 2, QTableWidgetItem(f"{p['Composite_Score']:.2f}"))
            self.recommendation_table.setItem(i, 3, QTableWidgetItem(f"{p['Normalized_VORP']:.2f}"))
            self.recommendation_table.setItem(i, 4, QTableWidgetItem(f"{p['Normalized_ADP']:.2f}"))
            self.recommendation_table.setItem(i, 5, QTableWidgetItem(f"{p['Normalized_Ceiling']:.2f}"))
            self.recommendation_table.setItem(i, 6, QTableWidgetItem(f"{p['Normalized_Opportunity']:.2f}"))
            self.recommendation_table.setItem(i, 7, QTableWidgetItem(f"{p['Injury_Score']:.2f}"))

    def reset_draft(self):
        for slot in self.roster_labels.values():
            if slot.player_label:
                slot.player_label.mark_as_available()
            slot.clear_slot()
        if os.path.exists(self.SAVE_FILE):
            os.remove(self.SAVE_FILE)
        self.current_pick_index = 0
        self.snake_direction = 1
        self.setWindowTitle(f"Draft Tool - Current Pick: {self.draft_order[0]}")
        self.update_best_pick_label()

    def save_progress(self):
        data = {
            "current_pick_index": self.current_pick_index,
            "snake_direction": self.snake_direction,
            "rosters": {
                key: slot.player_label.player_id
                for key, slot in self.roster_labels.items()
                if slot.player_label
            }
        }
        with open(self.SAVE_FILE, 'w') as f:
            json.dump(data, f)

    def load_progress(self):
        if not os.path.exists(self.SAVE_FILE):
            return
        try:
            with open(self.SAVE_FILE, 'r') as f:
                data = json.load(f)
            self.current_pick_index = data.get("current_pick_index", 0)
            self.snake_direction = data.get("snake_direction", 1)
            for key, pid in data.get("rosters", {}).items():
                for label in self.player_labels:
                    if label.player_id == pid:
                        if key in self.roster_labels:
                            self.roster_labels[key].setText(label.player_name.split(" (")[0])
                            self.roster_labels[key].player_label = label
                            label.mark_as_assigned()
        except Exception as e:
            print(f"Error loading draft progress: {e}")

    def on_manual_load(self):
        self.reset_draft()
        self.load_progress()
        self.update_best_pick_label()
        self.update_player_list()




if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    processed_data_dir = os.path.join(base_dir, 'data', 'processed')
    ranked_players_path = os.path.join(processed_data_dir, 'merged_data_v2.csv')

    app = QApplication(sys.argv)
    window = PlayerDisplayApp(ranked_players_path)
    window.show()
    sys.exit(app.exec_())

