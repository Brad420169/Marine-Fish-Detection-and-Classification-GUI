"""Readable evaluation of saved review decisions."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)


def score(value):
    return 'N/A' if value is None else f'{value:.2f}'


class EvaluationPage(QDialog):
    def __init__(self, report, parent=None):
        super().__init__(parent)
        self.report = report
        self.setWindowTitle('AI model evaluation')
        self.resize(1000, 780)
        layout = QVBoxLayout(self)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        body = QWidget(); self.body = QVBoxLayout(body); self.body.setSpacing(14)
        scroll.setWidget(body); layout.addWidget(scroll)
        self.label('AI model evaluation', 'pageTitle')
        self.label('Original AI predictions compared with your saved, confirmed review.')
        reviewed, total = report['reviewed_frames'], report['total_frames']
        self.coverage = self.label(
            f'{"Review complete" if report["complete"] else "Partial review"} · {reviewed:,} of {total:,} frames confirmed')
        bar = QProgressBar(); bar.setRange(0, max(total, 1)); bar.setValue(reviewed)
        self.body.addWidget(bar)
        self.label('Scores use confirmed frames only. Unconfirmed edits are excluded. '
                   'Confirm & Next checks every fish in the frame, including unchanged predictions and empty frames. '
                   'For older reviews, confirm the frames again to record full-frame completion.')
        if report.get('legacy_frames'):
            self.label(f'{report["legacy_frames"]:,} frames come from older saved reviews and are included provisionally. '
                       'Their saved review images indicate a prior confirmation, but completeness cannot be verified. '
                       'Confirm them again before treating these as final scores.')
        if not report['complete']:
            self.label('These are provisional scores for the reviewed subset. Flagged frames are a biased sample; '
                       'turn off “Review frames only” and review the whole video for a full-run evaluation.')
        self.mode = QComboBox()
        self.mode.addItems(['Detection + species', 'Fish detection'])
        self.body.addWidget(self.mode)
        self.explanation = self.label('')
        cards = QHBoxLayout(); self.body.addLayout(cards)
        self.cards = {}
        for key, title in [('precision', 'Precision'), ('recall', 'Recall'), ('f1', 'F1 score')]:
            box = QGroupBox(title); column = QVBoxLayout(box)
            value = QLabel(); value.setObjectName('pageTitle'); column.addWidget(value)
            description = QLabel(); description.setWordWrap(True); column.addWidget(description)
            counts = QLabel(); counts.setWordWrap(True); counts.setObjectName('muted'); column.addWidget(counts)
            cards.addWidget(box, 1); self.cards[key] = (value, description, counts)
        counts = report['counts']
        self.label('What changed in the confirmed frames', 'cardTitle')
        grid = QGridLayout(); self.body.addLayout(grid)
        for index, (title, key) in enumerate([('Correct predictions', 'correct'), ('Species corrected', 'corrected'),
                                            ('Predictions rejected', 'rejected'), ('Missed fish added', 'missed')]):
            label = QLabel(f'{counts[key]:,}  {title}'); grid.addWidget(label, index // 2, index % 2)
        self.label('Per-species results', 'cardTitle')
        self.label('A species correction counts as a false positive for the predicted species and a false negative '
                   'for the reviewed species. Overall Detection + species scores pool these counts across species.')
        self.table = QTableWidget(len(report['species']), 7)
        self.table.setHorizontalHeaderLabels(['Species', 'Correct', 'False positives', 'Missed / wrong species', 'Precision', 'Recall', 'F1'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        for row, (name, values) in enumerate(report['species'].items()):
            for col, text in enumerate([name, str(values['tp']), str(values['fp']), str(values['fn']),
                                         score(values['precision']), score(values['recall']), score(values['f1'])]):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeColumnsToContents(); self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(190); self.body.addWidget(self.table)
        self.label('How to read these scores', 'cardTitle')
        self.label('Precision = correct predictions ÷ all predictions. Recall = correctly detected fish ÷ all reviewed fish. '
                   'F1 balances precision and recall. N/A means there is no denominator. '
                   'Counts represent fish observations per frame, not unique fish or tracks. '
                   'Your review is the reference: add every missed fish and reject duplicate or spurious boxes. '
                   'This evaluates detection and species decisions, not bounding-box overlap (IoU) or mAP.')
        self.body.addStretch()
        close = QPushButton('Back to review'); close.clicked.connect(self.accept); layout.addWidget(close)
        self.mode.currentIndexChanged.connect(self.update_scores)
        self.update_scores()

    def label(self, text, name=None):
        label = QLabel(text); label.setWordWrap(True); label.setTextFormat(Qt.TextFormat.PlainText)
        if name: label.setObjectName(name)
        self.body.addWidget(label)
        return label

    def update_scores(self):
        species_mode = self.mode.currentIndex() == 0
        values = self.report['classification' if species_mode else 'detection']
        self.explanation.setText(
            'A prediction is correct only when both the fish and its species are correct. Species corrections reduce precision and recall.'
            if species_mode else 'A fish counts as detected even if its species needed correction. Only rejected predictions and missed fish reduce these scores.')
        p, r, f = values['precision'], values['recall'], values['f1']
        descriptions = {
            'precision': ('No AI predictions in the confirmed frames.' if p is None else
                          f'{p:.0%} of predictions {"detected a fish with the correct species" if species_mode else "correctly detected a fish"}.'),
            'recall': ('No reviewed fish in the confirmed frames.' if r is None else
                       f'{1-r:.0%} of fish {"were missed or assigned the wrong species" if species_mode else "were not detected"}.'),
            'f1': 'No predictions or reviewed fish to score.' if f is None else 'A combined measure of precision and recall; closer to 1 is better.'}
        tp, fp, fn = (values[k] for k in ('tp', 'fp', 'fn'))
        fractions = {'precision': f'{tp:,} correct / {tp+fp:,} predictions',
                     'recall': f'{tp:,} correct / {tp+fn:,} reviewed fish',
                     'f1': '2 × precision × recall / (precision + recall)'}
        for key, (value, description, count) in self.cards.items():
            value.setText(score(values[key])); description.setText(descriptions[key]); count.setText(fractions[key])
