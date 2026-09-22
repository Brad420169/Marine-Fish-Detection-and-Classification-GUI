"""Readable evaluation of saved review decisions."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QGridLayout,
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
        self.label('Detection precision checks whether predictions locate fish. Recall checks whether fish from trained species were found. '
                   'Species accuracy checks their labels only after detection.')
        if not report['class_list_available']:
            self.label('The saved model class list is unavailable. Detection recall and species accuracy are N/A; '
                       'run inference again to record the class list. Detection precision remains available.')
        else:
            outside = report['outside']
            self.label(f"Outside trained classes: {outside['detected']:,} detected, {outside['missed']:,} missed. "
                       'These observations are excluded from trained-species recall and accuracy. Detected fish still count toward detection precision.')
        cards = QHBoxLayout(); self.body.addLayout(cards)
        self.cards = {}
        for key, title in [('precision', 'Detection precision'), ('recall', 'Detection recall'), ('accuracy', 'Species accuracy')]:
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
        self.label('Trained-species results', 'cardTitle')
        self.label('A species correction counts as a false positive for the predicted species and a false negative '
                   'for the reviewed species when it is trained. This table measures species-aware precision and recall; it differs from the detection scores above.')
        self.table = QTableWidget(len(report['species']), 7)
        self.table.setHorizontalHeaderLabels(['Species', 'Correct', 'False positives', 'Missed or misidentified', 'Precision', 'Recall', 'F1'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        for row, (name, values) in enumerate(report['species'].items()):
            for col, text in enumerate([name, str(values['tp']), str(values['fp']), str(values['fn']),
                                         score(values['precision']), score(values['recall']), score(values['f1'])]):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeColumnsToContents(); self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(190); self.body.addWidget(self.table)
        self.label('How to read these scores', 'cardTitle')
        self.label('Detection precision = fish detected ÷ all predictions. Detection recall = trained-species fish detected ÷ all reviewed trained-species fish. '
                   'Species accuracy = correct species labels ÷ detected trained-species fish. Missed fish are excluded from species accuracy. N/A means no eligible observations or missing class information. '
                   'Counts represent fish observations per frame, not unique fish or tracks. '
                   'Your review is the reference: add every missed fish and reject duplicate or spurious boxes. '
                   'This evaluates detection and species decisions, not bounding-box overlap (IoU) or mAP.')
        self.body.addStretch()
        close = QPushButton('Back to review'); close.clicked.connect(self.accept); layout.addWidget(close)
        self.update_scores()

    def label(self, text, name=None):
        label = QLabel(text); label.setWordWrap(True); label.setTextFormat(Qt.TextFormat.PlainText)
        if name: label.setObjectName(name)
        self.body.addWidget(label)
        return label

    def update_scores(self):
        values = self.report['detection']
        p, r, accuracy = values['precision'], values['recall'], self.report['species_accuracy']
        descriptions = {
            'precision': ('No AI predictions in the confirmed frames.' if p is None else
                          f'{p:.0%} of predictions correctly located a fish.'),
            'recall': ('No eligible trained-species fish, or the model class list is unavailable.' if r is None else
                       f'{1-r:.0%} of fish from trained species were not detected.'),
            'accuracy': ('No detected trained-species fish, or the model class list is unavailable.' if accuracy is None else
                         f'{accuracy:.0%} of detected fish from trained species were identified correctly.')}
        detected, missed = values['trained_detected'], values['trained_missed']
        fractions = {'precision': f"{values['detected']:,} fish / {values['predictions']:,} predictions",
                     'recall': f'{detected:,} detected / {detected+missed:,} trained-species fish',
                     'accuracy': f"{self.report['species_correct']:,} correct labels / {detected:,} detected trained-species fish"}
        scores = dict(precision=p, recall=r, accuracy=accuracy)
        for key, (value, description, count) in self.cards.items():
            value.setText(score(scores[key])); description.setText(descriptions[key]); count.setText(fractions[key])
