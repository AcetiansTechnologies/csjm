import sys
import threading
import speech_recognition as sr
from gtts import gTTS
import pygame
from openai import OpenAI
import uuid, os, re, cv2
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QLineEdit, QFrame,
    QStackedWidget, QComboBox, QScrollArea, QTextEdit,
    QGraphicsDropShadowEffect
)
from PyQt6.QtGui import (
    QImage, QPixmap, QColor, QFont, QPainter,
    QBrush, QLinearGradient, QRadialGradient
)
from PyQt6.QtCore import (
    QTimer, Qt, QPropertyAnimation, QEasingCurve,
    pyqtProperty, pyqtSignal, QObject
)

# ── OpenAI Key ───────────────────────────────────────────
try:
    from config import OAI as OAI_KEY
except ImportError:
    OAI_KEY = "sk-YOUR-KEY-HERE"

pygame.mixer.init()

# ══════════════════════════════════════════════════════════
#  DESIGN TOKENS
# ══════════════════════════════════════════════════════════
C_BG         = "#070C14"
C_SURFACE    = "#0C1422"
C_CARD       = "#101C2E"
C_BORDER     = "#182840"
C_BORDER2    = "#1E3550"
C_ACCENT     = "#00D4A0"
C_ACCENT2    = "#00A882"
C_ACCENT_DIM = "#003D2E"
C_TEXT       = "#D8E8F4"
C_TEXT_MID   = "#6A8AA8"
C_TEXT_DIM   = "#2A4060"
C_RED        = "#FF3E5A"
C_YELLOW     = "#F5C842"
C_BLUE       = "#3A8EF6"
C_PURPLE     = "#C45EFF"

FONT_MAIN = "'Segoe UI Variable', 'Segoe UI', 'SF Pro Display', sans-serif"
FONT_MONO = "'Cascadia Code', 'Consolas', 'JetBrains Mono', monospace"

# ══════════════════════════════════════════════════════════
#  MULANK (Numerology root number from DOB)
# ══════════════════════════════════════════════════════════
def compute_mulank(dob_str: str) -> int:
    """
    Reduce day of birth to single digit (1-9).
    Accepts DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD, etc.
    """
    digits = re.sub(r"\D", "", dob_str)
    if len(digits) >= 2:
        day = int(digits[:2])
    else:
        return 0
    while day > 9:
        day = sum(int(d) for d in str(day))
    return day

MULANK_TRAITS = {
    1: "Leadership, independence, strong will",
    2: "Sensitivity, diplomacy, cooperation",
    3: "Creativity, expression, social energy",
    4: "Discipline, stability, practicality",
    5: "Freedom-seeking, adaptability, curiosity",
    6: "Nurturing, responsibility, harmony",
    7: "Introspection, analysis, spiritual depth",
    8: "Ambition, authority, material focus",
    9: "Compassion, idealism, broad vision",
}

# ══════════════════════════════════════════════════════════
#  SIGNALS
# ══════════════════════════════════════════════════════════
class SessionSignals(QObject):
    question_started  = pyqtSignal(int, str)
    listening_started = pyqtSignal(int)
    answer_received   = pyqtSignal(int, str)
    session_complete  = pyqtSignal(str)
    status_update     = pyqtSignal(str)
    error_occurred    = pyqtSignal(str)


# ══════════════════════════════════════════════════════════
#  VOICE ENGINE
# ══════════════════════════════════════════════════════════
class VoiceInterviewEngine:
    def __init__(self, signals: SessionSignals, api_key: str,
                 lang: str, candidate: dict):
        self.signals    = signals
        self.client     = OpenAI(api_key=api_key)
        self.recognizer = sr.Recognizer()
        self.recognizer.pause_threshold  = 1.2
        self.recognizer.energy_threshold = 300
        self.lang       = lang          # "en" or "hi"
        self.candidate  = candidate     # name, dob, email, mulank

    # ── Generate ≤10-word questions ──────────────────────
    def generate_questions(self) -> list[str]:
        self.signals.status_update.emit("Generating questions…")
        mulank = self.candidate.get("mulank", 0)
        traits = MULANK_TRAITS.get(mulank, "general psychological traits")
        name   = self.candidate.get("name", "the candidate")

        if self.lang == "hi":
            lang_instr = (
                "Generate exactly 5 psychological interview questions in Hindi. "
                "Each question MUST be 10 words or fewer (count Hindi words). "
                "No numbering, one question per line."
            )
        else:
            lang_instr = (
                "Generate exactly 5 psychological interview questions in English. "
                "Each question MUST be 10 words or fewer. "
                "No numbering, one question per line."
            )

        prompt = (
            f"Candidate name: {name}. "
            f"Their numerology Mulank is {mulank}, associated with: {traits}. "
            f"{lang_instr} "
            "Topics must cover: self-perception, stress, emotions, relationships, motivation. "
            "Questions should be personal, introspective, and relevant to the Mulank traits."
        )
        resp = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        raw   = resp.choices[0].message.content.strip()
        lines = [l.strip() for l in raw.split("\n") if l.strip()]
        # Strip "1." / "1)" prefixes if any
        questions = []
        for l in lines:
            for sep in [". ", ") ", ": "]:
                if len(l) > 2 and l[1] in sep:
                    l = l.split(sep, 1)[-1].strip()
                    break
            if l:
                questions.append(l)
        return questions[:5]

    # ── TTS ───────────────────────────────────────────────
    def speak(self, text: str):
        self.signals.status_update.emit("Speaking…")
        tts_lang = "hi" if self.lang == "hi" else "en"
        tmp = f"_tts_{uuid.uuid4().hex}.mp3"
        try:
            gTTS(text, lang=tts_lang).save(tmp)
            pygame.mixer.music.load(tmp)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.wait(50)
        finally:
            try:
                pygame.mixer.music.unload()
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    # ── STT ───────────────────────────────────────────────
    def listen(self) -> str:
        self.signals.status_update.emit("Listening… (speak now)")
        recog_lang = "hi-IN" if self.lang == "hi" else "en-US"
        with sr.Microphone() as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
            try:
                audio = self.recognizer.listen(source, timeout=12, phrase_time_limit=30)
            except sr.WaitTimeoutError:
                return "[No response detected]"
        try:
            return self.recognizer.recognize_google(audio, language=recog_lang)
        except sr.UnknownValueError:
            return "[Could not understand]"
        except sr.RequestError as e:
            return f"[API error: {e}]"

    # ── Report (<300 words) ───────────────────────────────
    def generate_report(self, qa_pairs: list[tuple[str, str]]) -> str:
        self.signals.status_update.emit("Generating report…")
        c = self.candidate
        mulank = c.get("mulank", 0)
        traits = MULANK_TRAITS.get(mulank, "")
        transcript = "\n".join(
            f"Q{i+1}: {q}\nA{i+1}: {a}" for i, (q, a) in enumerate(qa_pairs)
        )
        report_lang = "Hindi" if self.lang == "hi" else "English"
        prompt = (
            f"Candidate: {c.get('name')}, DOB: {c.get('dob')}, "
            f"Email: {c.get('email')}, Mulank: {mulank} ({traits}).\n\n"
            f"Interview Transcript:\n{transcript}\n\n"
            f"Write a psychological evaluation report in {report_lang}. "
            "STRICT LIMIT: under 300 words total. "
            "Structure:\n"
            "MULANK INSIGHT (1-2 sentences about numerology)\n"
            "EMOTIONAL PROFILE (2-3 sentences)\n"
            "STRESS & COPING (1-2 sentences)\n"
            "INTERPERSONAL STYLE (1-2 sentences)\n"
            "OVERALL ASSESSMENT (2-3 sentences)\n"
            "RECOMMENDATIONS (2 bullet points, one line each)\n"
            "Be concise, evidence-based, and professional."
        )
        resp = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
        )
        return resp.choices[0].message.content.strip()

    # ── Main loop ─────────────────────────────────────────
    def run_session(self):
        try:
            questions = self.generate_questions()
            qa_pairs  = []
            name = self.candidate.get("name", "")

            greeting = (
                f"नमस्ते {name}। मैं आपसे कुछ प्रश्न पूछूँगा।"
                if self.lang == "hi"
                else f"Hello {name}. I will ask you five questions."
            )
            self.speak(greeting)

            for idx, question in enumerate(questions):
                self.signals.question_started.emit(idx, question)
                self.speak(f"{'प्रश्न' if self.lang == 'hi' else 'Question'} {idx+1}. {question}")
                self.signals.listening_started.emit(idx)
                answer = self.listen()
                qa_pairs.append((question, answer))
                self.signals.answer_received.emit(idx, answer)
                if idx < len(questions) - 1:
                    ack = "धन्यवाद। अगला प्रश्न।" if self.lang == "hi" else "Thank you. Next question."
                    self.speak(ack)

            closing = (
                "धन्यवाद। आपकी रिपोर्ट तैयार हो रही है।"
                if self.lang == "hi"
                else "Thank you. Preparing your report now."
            )
            self.speak(closing)
            report = self.generate_report(qa_pairs)
            self.signals.session_complete.emit(report)

        except Exception as exc:
            self.signals.error_occurred.emit(str(exc))


# ══════════════════════════════════════════════════════════
#  UI HELPERS
# ══════════════════════════════════════════════════════════
class PulsingDot(QWidget):
    def __init__(self, color=C_RED, parent=None):
        super().__init__(parent)
        self.color = color
        self.setFixedSize(12, 12)
        self._r, self._dir = 4.0, 0.05
        t = QTimer(self); t.timeout.connect(self._tick); t.start(40)

    def _tick(self):
        self._r += self._dir
        if self._r >= 6 or self._r <= 3: self._dir *= -1
        self.update()

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QRadialGradient(6, 6, 8); g.setColorAt(0, QColor(self.color+"60")); g.setColorAt(1, QColor(self.color+"00"))
        p.setBrush(QBrush(g)); p.setPen(Qt.PenStyle.NoPen); p.drawEllipse(-2, -2, 16, 16)
        p.setBrush(QBrush(QColor(self.color))); r = int(self._r)
        p.drawEllipse(6-r//2, 6-r//2, r, r)


class ScanLine(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._y = 0
        t = QTimer(self); t.timeout.connect(self._tick); t.start(16)

    def _tick(self): self._y = (self._y + 2) % (self.height() + 40); self.update()

    def paintEvent(self, e):
        if not self.height(): return
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QLinearGradient(0, self._y-20, 0, self._y+20)
        for pos, a in [(0.0,0),(0.4,30),(0.5,80),(0.6,30),(1.0,0)]:
            g.setColorAt(pos, QColor(0, 212, 160, a))
        p.fillRect(0, self._y-20, self.width(), 40, g)
        from PyQt6.QtGui import QPen
        p.setPen(QPen(QColor(C_ACCENT+"AA"), 2))
        L, w, h = 20, self.width(), self.height()
        for x1,y1,x2,y2 in [(8,8,8+L,8),(8,8,8,8+L),(w-8,8,w-8-L,8),(w-8,8,w-8,8+L),
                             (8,h-8,8+L,h-8),(8,h-8,8,h-8-L),(w-8,h-8,w-8-L,h-8),(w-8,h-8,w-8,h-8-L)]:
            p.drawLine(x1,y1,x2,y2)


class CameraWidget(QLabel):
    def __init__(self):
        super().__init__()
        self.setFixedSize(480, 340)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(f"background:{C_SURFACE};border-radius:16px;border:1.5px solid {C_BORDER};")
        self.cap = cv2.VideoCapture(0)
        QTimer(self, timeout=self._update, interval=30).start()
        self.scan = ScanLine(self); self.scan.setGeometry(0,0,480,340)
        # REC badge
        rf = QFrame(self); rf.setGeometry(12,12,68,24)
        rf.setStyleSheet(f"background:rgba(10,20,30,200);border-radius:6px;border:1px solid {C_BORDER};")
        rl = QHBoxLayout(rf); rl.setContentsMargins(6,0,8,0); rl.setSpacing(4)
        rl.addWidget(PulsingDot(C_RED, rf))
        lbl = QLabel("REC"); lbl.setStyleSheet(f"color:{C_TEXT};font-size:10px;font-weight:700;font-family:{FONT_MONO};background:transparent;border:none;")
        rl.addWidget(lbl)
        # LIVE badge
        lf = QFrame(self); lf.setGeometry(400,12,68,24)
        lf.setStyleSheet(f"background:rgba(0,212,160,18);border-radius:6px;border:1px solid {C_ACCENT}55;")
        ll = QHBoxLayout(lf); ll.setContentsMargins(8,0,8,0)
        ll.addWidget(PulsingDot(C_ACCENT, lf))
        ll2 = QLabel("LIVE"); ll2.setStyleSheet(f"color:{C_ACCENT};font-size:10px;font-weight:700;font-family:{FONT_MONO};background:transparent;border:none;")
        ll.addWidget(ll2)
        # Clock
        self._tlbl = QLabel("00:00:00", self); self._tlbl.setGeometry(0,310,480,28)
        self._tlbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tlbl.setStyleSheet(f"color:{C_ACCENT}AA;font-family:{FONT_MONO};font-size:11px;font-weight:600;background:transparent;letter-spacing:3px;")
        self._elapsed = 0
        QTimer(self, timeout=self._tick_clock, interval=1000).start()

    def _tick_clock(self):
        self._elapsed += 1
        h,rem = divmod(self._elapsed,3600); m,s = divmod(rem,60)
        self._tlbl.setText(f"{h:02d}:{m:02d}:{s:02d}")

    def _update(self):
        ret, frame = self.cap.read()
        if ret:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h,w,ch = frame.shape
            img = QImage(frame.data,w,h,ch*w,QImage.Format.Format_RGB888)
            pix = QPixmap.fromImage(img)
            scaled = pix.scaled(self.width(),self.height(),Qt.AspectRatioMode.KeepAspectRatioByExpanding,Qt.TransformationMode.SmoothTransformation)
            x=(scaled.width()-self.width())//2; y=(scaled.height()-self.height())//2
            self.setPixmap(scaled.copy(x,y,self.width(),self.height()))

    def release(self): self.cap.release()


def make_field(placeholder, height=42):
    f = QLineEdit(); f.setPlaceholderText(placeholder); f.setFixedHeight(height)
    f.setStyleSheet(f"QLineEdit{{background:{C_BG};border:1.5px solid {C_BORDER};border-radius:10px;color:{C_TEXT};padding:0 14px;font-family:{FONT_MAIN};font-size:13px;}}QLineEdit:focus{{border:1.5px solid {C_ACCENT};background:#0A1828;}}")
    return f

def make_card(r=14):
    f = QFrame(); f.setStyleSheet(f"QFrame{{background:{C_CARD};border-radius:{r}px;border:1px solid {C_BORDER};}}")
    return f

def section_label(text, color=C_ACCENT):
    l = QLabel(text); l.setStyleSheet(f"color:{color};font-family:{FONT_MONO};font-size:10px;font-weight:700;letter-spacing:3px;")
    return l

def make_combo(items):
    c = QComboBox(); c.addItems(items); c.setFixedHeight(38)
    c.setStyleSheet(f"QComboBox{{background:{C_BG};border:1.5px solid {C_BORDER};border-radius:9px;color:{C_TEXT};padding:0 12px;font-family:{FONT_MAIN};font-size:12px;}}QComboBox:focus{{border:1.5px solid {C_ACCENT};}}QComboBox::drop-down{{border:none;width:28px;}}QComboBox::down-arrow{{image:none;border-left:4px solid transparent;border-right:4px solid transparent;border-top:5px solid {C_TEXT_MID};margin-right:10px;}}QComboBox QAbstractItemView{{background:{C_CARD};border:1px solid {C_BORDER};color:{C_TEXT};selection-background-color:{C_ACCENT_DIM};border-radius:8px;}}")
    return c

class ToggleSwitch(QWidget):
    toggled = pyqtSignal(bool)
    def __init__(self, default=True, parent=None):
        super().__init__(parent); self.setFixedSize(46,24)
        self._on=default; self._x=22.0 if default else 2.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim=QPropertyAnimation(self,b"_handle_x"); self._anim.setDuration(180); self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    def get_x(self): return self._x
    def set_x(self,v): self._x=v; self.update()
    _handle_x=pyqtProperty(float,get_x,set_x)
    def mousePressEvent(self,e):
        self._on=not self._on; self._anim.setStartValue(self._x); self._anim.setEndValue(22.0 if self._on else 2.0); self._anim.start(); self.toggled.emit(self._on)
    def paintEvent(self,e):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(C_ACCENT if self._on else C_BORDER2))); p.setPen(Qt.PenStyle.NoPen); p.drawRoundedRect(0,4,46,16,8,8)
        p.setBrush(QBrush(QColor("#FFF"))); p.drawEllipse(int(self._x),2,20,20)

class NavButton(QPushButton):
    def __init__(self, icon, label):
        super().__init__(); self.setFixedSize(200,46); self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon=icon; self._label=label; self._active=False; self._upd()
    def set_active(self,v): self._active=v; self._upd()
    def _upd(self):
        if self._active:
            self.setStyleSheet(f"QPushButton{{background:{C_ACCENT_DIM};border-left:3px solid {C_ACCENT};border-right:none;border-top:none;border-bottom:none;border-radius:0;color:{C_ACCENT};font-family:{FONT_MAIN};font-size:13px;font-weight:600;text-align:left;padding-left:20px;}}")
        else:
            self.setStyleSheet(f"QPushButton{{background:transparent;border:none;color:{C_TEXT_MID};font-family:{FONT_MAIN};font-size:13px;font-weight:500;text-align:left;padding-left:23px;border-radius:0;}}QPushButton:hover{{background:{C_SURFACE};color:{C_TEXT};}}")
        self.setText(f"  {self._icon}   {self._label}")

def settings_row(label, desc, widget):
    row=QWidget(); row.setStyleSheet("background:transparent;")
    rl=QHBoxLayout(row); rl.setContentsMargins(0,0,0,0); rl.setSpacing(12)
    txt=QWidget(); txt.setStyleSheet("background:transparent;")
    tl=QVBoxLayout(txt); tl.setContentsMargins(0,0,0,0); tl.setSpacing(2)
    tl.addWidget(QLabel(label,styleSheet=f"color:{C_TEXT};font-family:{FONT_MAIN};font-size:13px;font-weight:500;background:transparent;"))
    tl.addWidget(QLabel(desc, styleSheet=f"color:{C_TEXT_MID};font-family:{FONT_MAIN};font-size:11px;background:transparent;"))
    rl.addWidget(txt,1); rl.addWidget(widget,0,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
    return row


# ══════════════════════════════════════════════════════════
#  QUESTION CARD
# ══════════════════════════════════════════════════════════
class QuestionCard(QFrame):
    STATES = {
        "idle":      (C_CARD,       C_BORDER,  C_TEXT_DIM, "○", "IDLE"),
        "active":    (C_ACCENT_DIM, C_ACCENT,  C_ACCENT,   "◎", "ASKING"),
        "listening": ("#001828",    C_BLUE,    C_BLUE,     "◉", "LISTENING"),
        "done":      ("#001A14",    C_ACCENT2, C_TEXT_MID, "◈", "ANSWERED"),
        "error":     ("#1A0008",    C_RED,     C_RED,      "✕", "ERROR"),
    }
    def __init__(self, number):
        super().__init__(); self.number=number; self.setMinimumHeight(64)
        lay=QVBoxLayout(self); lay.setContentsMargins(14,10,14,10); lay.setSpacing(5)
        top=QHBoxLayout(); top.setSpacing(8)
        self._icon=QLabel("○"); self._icon.setFixedWidth(18); self._icon.setStyleSheet("font-size:13px;background:transparent;")
        self._num=QLabel(f"Q{number}"); self._num.setFixedWidth(26); self._num.setStyleSheet(f"color:{C_TEXT_DIM};font-family:{FONT_MONO};font-size:10px;font-weight:700;background:transparent;")
        self._qtxt=QLabel("Waiting…"); self._qtxt.setWordWrap(True); self._qtxt.setStyleSheet(f"color:{C_TEXT_DIM};font-family:{FONT_MAIN};font-size:12px;background:transparent;")
        self._badge=QLabel("IDLE"); self._badge.setFixedHeight(18); self._badge.setStyleSheet(f"color:{C_TEXT_DIM};background:{C_BORDER};border-radius:4px;padding:0 7px;font-family:{FONT_MONO};font-size:9px;font-weight:700;letter-spacing:1px;")
        top.addWidget(self._icon); top.addWidget(self._num); top.addWidget(self._qtxt,1); top.addWidget(self._badge)
        self._atxt=QLabel(); self._atxt.setWordWrap(True); self._atxt.setVisible(False)
        self._atxt.setStyleSheet(f"color:{C_TEXT_MID};font-family:{FONT_MAIN};font-size:11px;font-style:italic;background:transparent;padding-left:44px;")
        lay.addLayout(top); lay.addWidget(self._atxt)
        self.set_state("idle")

    def set_state(self, state, question=None, answer=None):
        bg,border,color,icon,badge = self.STATES.get(state, self.STATES["idle"])
        self.setStyleSheet(f"QFrame{{background:{bg};border-radius:11px;border:1.5px solid {border};}}")
        self._icon.setText(icon); self._icon.setStyleSheet(f"font-size:13px;color:{color};background:transparent;")
        self._num.setStyleSheet(f"color:{color};font-family:{FONT_MONO};font-size:10px;font-weight:700;background:transparent;")
        self._badge.setText(badge); self._badge.setStyleSheet(f"color:{color};background:{bg};border:1px solid {border};border-radius:4px;padding:0 7px;font-family:{FONT_MONO};font-size:9px;font-weight:700;letter-spacing:1px;")
        if question:
            self._qtxt.setText(question); self._qtxt.setStyleSheet(f"color:{color};font-family:{FONT_MAIN};font-size:12px;background:transparent;")
        if answer:
            self._atxt.setText(f"↳  {answer}"); self._atxt.setVisible(True)

    def reset(self):
        self.set_state("idle"); self._atxt.setVisible(False); self._qtxt.setText("Waiting…")


# ══════════════════════════════════════════════════════════
#  MULANK BADGE WIDGET
# ══════════════════════════════════════════════════════════
class MulankBadge(QFrame):
    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"QFrame{{background:{C_CARD};border-radius:12px;border:1px solid {C_BORDER};}}")
        lay = QHBoxLayout(self); lay.setContentsMargins(16,12,16,12); lay.setSpacing(14)
        # Big number
        self._num = QLabel("—")
        self._num.setFixedSize(52,52)
        self._num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._num.setStyleSheet(f"color:{C_ACCENT};font-family:{FONT_MONO};font-size:26px;font-weight:800;background:{C_ACCENT_DIM};border-radius:26px;border:2px solid {C_ACCENT}55;")
        # Text block
        right = QVBoxLayout(); right.setSpacing(3)
        lbl = QLabel("MULANK  (मूलांक)")
        lbl.setStyleSheet(f"color:{C_ACCENT};font-family:{FONT_MONO};font-size:9px;font-weight:700;letter-spacing:2px;background:transparent;")
        self._trait = QLabel("Enter DOB to calculate")
        self._trait.setWordWrap(True)
        self._trait.setStyleSheet(f"color:{C_TEXT_MID};font-family:{FONT_MAIN};font-size:11px;background:transparent;")
        right.addWidget(lbl); right.addWidget(self._trait)
        lay.addWidget(self._num); lay.addLayout(right,1)

    def update_mulank(self, dob_str: str):
        m = compute_mulank(dob_str)
        if m:
            self._num.setText(str(m))
            self._trait.setText(MULANK_TRAITS.get(m, ""))
        else:
            self._num.setText("—")
            self._trait.setText("Enter DOB to calculate")


# ══════════════════════════════════════════════════════════
#  MAIN SESSION PAGE
# ══════════════════════════════════════════════════════════
class SessionPage(QWidget):
    def __init__(self, api_key: str, settings_ref):
        super().__init__()
        self.api_key      = api_key
        self.settings_ref = settings_ref   # to read language setting
        self.signals      = SessionSignals()
        self._running     = False
        self._report      = ""
        self.setStyleSheet("background:transparent;")
        self._build()
        self._connect_signals()

    def _build(self):
        root = QHBoxLayout(self); root.setContentsMargins(24,24,24,24); root.setSpacing(20)

        # ── LEFT: Camera + stats ──────────────────────────
        left = QVBoxLayout(); left.setSpacing(0)

        title = QLabel("Psychological Analysis")
        title.setStyleSheet(f"color:{C_TEXT};font-family:{FONT_MAIN};font-size:21px;font-weight:700;letter-spacing:-0.5px;")
        sub = QLabel("AI Voice Interview  ·  Live Evaluation")
        sub.setStyleSheet(f"color:{C_TEXT_DIM};font-family:{FONT_MONO};font-size:10px;letter-spacing:2px;margin-bottom:14px;")
        left.addWidget(title); left.addWidget(sub)

        self.camera = CameraWidget()
        left.addWidget(self.camera)
        left.addSpacing(12)

        # Stats strip
        strip = QFrame(); strip.setFixedHeight(52)
        strip.setStyleSheet(f"background:{C_CARD};border-radius:11px;border:1px solid {C_BORDER};")
        sl = QHBoxLayout(strip); sl.setContentsMargins(14,0,14,0); sl.setSpacing(0)
        self._stats: dict[str, QLabel] = {}
        for icon,key,val,col in [("◈","QUESTIONS","5",C_ACCENT),("◉","ANSWERED","0",C_BLUE),("◎","CURRENT","—",C_YELLOW),("◍","STATUS","IDLE",C_TEXT_MID)]:
            cell=QVBoxLayout(); cell.setSpacing(1)
            v=QLabel(val); v.setStyleSheet(f"color:{col};font-family:{FONT_MONO};font-size:14px;font-weight:700;background:transparent;")
            k=QLabel(f"{icon} {key}"); k.setStyleSheet(f"color:{C_TEXT_DIM};font-family:{FONT_MONO};font-size:9px;letter-spacing:1.5px;background:transparent;")
            cell.addWidget(v,alignment=Qt.AlignmentFlag.AlignCenter); cell.addWidget(k,alignment=Qt.AlignmentFlag.AlignCenter)
            sl.addLayout(cell); self._stats[key]=v
            if key!="STATUS":
                d=QFrame(); d.setFrameShape(QFrame.Shape.VLine); d.setStyleSheet(f"color:{C_BORDER};max-width:1px;"); sl.addWidget(d)
        left.addWidget(strip); left.addStretch()

        # ── SEPARATOR ─────────────────────────────────────
        sep=QFrame(); sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"background:{C_BORDER};max-width:1px;border:none;"); sep.setFixedWidth(1)

        # ── RIGHT: Info + questions ───────────────────────
        right = QVBoxLayout(); right.setSpacing(10)

        # ── Candidate Info Card ───────────────────────────
        cand_card = make_card()
        ci = QVBoxLayout(cand_card); ci.setContentsMargins(16,14,16,16); ci.setSpacing(8)
        ci.addWidget(section_label("CANDIDATE INFO"))

        self.name_field  = make_field("Full Name")
        self.dob_field   = make_field("Date of Birth  (DD/MM/YYYY)")
        self.email_field = make_field("Email Address")

        # Mulank badge updates on DOB change
        self.mulank_badge = MulankBadge()
        self.dob_field.textChanged.connect(self.mulank_badge.update_mulank)

        for w in [self.name_field, self.dob_field, self.email_field]:
            ci.addWidget(w)
        ci.addWidget(self.mulank_badge)
        right.addWidget(cand_card)

        # ── Questions Card ────────────────────────────────
        q_card = make_card()
        qi = QVBoxLayout(q_card); qi.setContentsMargins(14,12,14,12); qi.setSpacing(7)

        qhdr = QHBoxLayout(); qhdr.addWidget(section_label("SESSION QUESTIONS")); qhdr.addStretch()
        self._q_prog = QLabel("0 / 5")
        self._q_prog.setStyleSheet(f"color:{C_TEXT_MID};font-family:{FONT_MONO};font-size:11px;background:transparent;")
        qhdr.addWidget(self._q_prog); qi.addLayout(qhdr)

        self.q_cards: list[QuestionCard] = []
        for i in range(1,6):
            qc = QuestionCard(i); qi.addWidget(qc); self.q_cards.append(qc)

        self._prog_bg = QFrame(); self._prog_bg.setFixedHeight(3)
        self._prog_bg.setStyleSheet(f"background:{C_BORDER};border-radius:2px;")
        self._prog_fill = QFrame(self._prog_bg); self._prog_fill.setFixedHeight(3)
        self._prog_fill.setStyleSheet(f"background:{C_ACCENT};border-radius:2px;"); self._prog_fill.setFixedWidth(0)
        qi.addWidget(self._prog_bg)
        right.addWidget(q_card)

        # ── Action Buttons ────────────────────────────────
        act = QHBoxLayout(); act.setSpacing(10)
        self.start_btn = QPushButton("▶  Start Session")
        self.start_btn.setFixedHeight(46); self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.setStyleSheet(f"QPushButton{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {C_ACCENT},stop:1 {C_ACCENT2});color:#020D09;border:none;border-radius:11px;font-family:{FONT_MAIN};font-size:13px;font-weight:700;}}QPushButton:hover{{background:#00F0B5;}}QPushButton:disabled{{background:{C_BORDER};color:{C_TEXT_DIM};}}")
        self.start_btn.clicked.connect(self._start)

        self.report_btn = QPushButton("📄  Report")
        self.report_btn.setFixedHeight(46); self.report_btn.setFixedWidth(120)
        self.report_btn.setCursor(Qt.CursorShape.PointingHandCursor); self.report_btn.setEnabled(False)
        self.report_btn.setStyleSheet(f"QPushButton{{background:transparent;color:{C_BLUE};border:1.5px solid {C_BLUE}55;border-radius:11px;font-family:{FONT_MAIN};font-size:12px;font-weight:600;}}QPushButton:hover{{background:rgba(58,142,246,15);border:1.5px solid {C_BLUE}AA;}}QPushButton:disabled{{color:{C_TEXT_DIM};border-color:{C_BORDER};}}")
        self.report_btn.clicked.connect(self._goto_report)
        act.addWidget(self.start_btn); act.addWidget(self.report_btn)
        right.addLayout(act)

        # ── Status Bar ────────────────────────────────────
        sf = QFrame(); sf.setFixedHeight(44)
        sf.setStyleSheet(f"background:{C_CARD};border-radius:11px;border:1px solid {C_BORDER};")
        sb = QHBoxLayout(sf); sb.setContentsMargins(12,0,12,0); sb.setSpacing(8)
        self._sdot = PulsingDot(C_TEXT_DIM)
        self._stxt = QLabel("Fill candidate info, then press Start")
        self._stxt.setStyleSheet(f"color:{C_TEXT_MID};font-family:{FONT_MONO};font-size:11px;font-weight:600;background:transparent;")
        self._stag = QLabel("IDLE")
        self._stag.setStyleSheet(f"color:{C_TEXT_DIM};background:{C_BORDER};border-radius:4px;padding:2px 8px;font-family:{FONT_MONO};font-size:9px;font-weight:700;letter-spacing:1.5px;")
        sb.addWidget(self._sdot); sb.addWidget(self._stxt,1); sb.addWidget(self._stag)
        right.addWidget(sf); right.addStretch()

        root.addLayout(left,5); root.addWidget(sep)
        rw=QWidget(); rw.setMinimumWidth(390); rw.setStyleSheet("background:transparent;"); rw.setLayout(right)
        root.addWidget(rw,4)

    def _connect_signals(self):
        s=self.signals
        s.question_started.connect(self._on_q_start)
        s.listening_started.connect(self._on_listen)
        s.answer_received.connect(self._on_answer)
        s.session_complete.connect(self._on_complete)
        s.status_update.connect(self._on_status)
        s.error_occurred.connect(self._on_error)

    # ── Slot handlers ─────────────────────────────────────
    def _set_stat(self, key, val, col):
        l=self._stats.get(key)
        if l: l.setText(val); l.setStyleSheet(f"color:{col};font-family:{FONT_MONO};font-size:14px;font-weight:700;background:transparent;")

    def _set_prog(self, ratio):
        self._prog_fill.setFixedWidth(max(0,int(self._prog_bg.width()*ratio)))

    def _set_status(self, txt, color, tag):
        self._sdot.color=color
        self._stxt.setText(txt); self._stxt.setStyleSheet(f"color:{color};font-family:{FONT_MONO};font-size:11px;font-weight:600;background:transparent;")
        self._stag.setText(tag); self._stag.setStyleSheet(f"color:{color};background:{color}22;border-radius:4px;padding:2px 8px;font-family:{FONT_MONO};font-size:9px;font-weight:700;letter-spacing:1.5px;border:1px solid {color}55;")

    def _on_q_start(self, idx, question):
        self.q_cards[idx].set_state("active", question=question)
        self._q_prog.setText(f"{idx+1} / 5")
        self._set_stat("CURRENT", f"Q{idx+1}", C_YELLOW)
        self._set_stat("STATUS", "ASKING", C_ACCENT)
        self._set_prog(idx/5)
        self._set_status(f"Asking Q{idx+1}: {question[:55]}…", C_ACCENT, "SPEAKING")

    def _on_listen(self, idx):
        self.q_cards[idx].set_state("listening")
        self._set_stat("STATUS", "LISTEN", C_BLUE)
        self._set_status(f"Listening for answer to Q{idx+1}…", C_BLUE, "LISTENING")

    def _on_answer(self, idx, answer):
        self.q_cards[idx].set_state("done", answer=answer)
        done = sum(1 for c in self.q_cards if c._badge.text()=="ANSWERED")
        self._set_stat("ANSWERED", str(done), C_BLUE)
        self._set_prog((idx+1)/5)

    def _on_complete(self, report):
        self._running=False; self._report=report
        self._set_stat("STATUS","DONE",C_ACCENT); self._set_prog(1.0)
        self.start_btn.setText("▶  Restart"); self.start_btn.setEnabled(True)
        self.report_btn.setEnabled(True)
        self._set_status("Session complete · Report ready", C_ACCENT, "COMPLETE")

    def _on_status(self, msg): self._stxt.setText(msg)

    def _on_error(self, msg):
        self._running=False; self.start_btn.setEnabled(True)
        self._set_status(f"Error: {msg}", C_RED, "ERROR")

    # ── Start ─────────────────────────────────────────────
    def _start(self):
        name  = self.name_field.text().strip()
        dob   = self.dob_field.text().strip()
        email = self.email_field.text().strip()
        if not name:
            self._set_status("Please enter candidate name", C_RED, "MISSING"); return
        if not dob:
            self._set_status("Please enter date of birth", C_RED, "MISSING"); return
        if not email:
            self._set_status("Please enter email address", C_RED, "MISSING"); return

        mulank = compute_mulank(dob)
        candidate = {"name": name, "dob": dob, "email": email, "mulank": mulank}
        lang = self.settings_ref.get_language()   # "en" or "hi"

        self._running=True
        for qc in self.q_cards: qc.reset()
        self._set_stat("ANSWERED","0",C_BLUE); self._set_stat("CURRENT","—",C_YELLOW)
        self._set_prog(0); self.start_btn.setEnabled(False); self.report_btn.setEnabled(False)
        self._report=""

        engine = VoiceInterviewEngine(self.signals, self.api_key, lang, candidate)
        threading.Thread(target=engine.run_session, daemon=True).start()

    def _goto_report(self):
        # Walk up to MainWindow
        self.window().show_report(self._report, self.name_field.text().strip(),
                                  self.dob_field.text().strip(), self.email_field.text().strip(),
                                  compute_mulank(self.dob_field.text().strip()))

    def release(self): self.camera.release()


# ══════════════════════════════════════════════════════════
#  REPORT PAGE
# ══════════════════════════════════════════════════════════
class ReportPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:transparent;")
        self._build()

    def _build(self):
        outer=QVBoxLayout(self); outer.setContentsMargins(28,24,28,24); outer.setSpacing(14)

        # Header
        hdr=QHBoxLayout()
        hdr.addWidget(QLabel("Psychological Report", styleSheet=f"color:{C_TEXT};font-family:{FONT_MAIN};font-size:21px;font-weight:700;"))
        hdr.addStretch()
        hdr.addWidget(QLabel("AI-Generated Evaluation", styleSheet=f"color:{C_TEXT_DIM};font-family:{FONT_MONO};font-size:10px;letter-spacing:2px;"))
        outer.addLayout(hdr)

        # Candidate summary strip
        self.info_strip = QFrame()
        self.info_strip.setFixedHeight(64)
        self.info_strip.setStyleSheet(f"background:{C_CARD};border-radius:12px;border:1px solid {C_BORDER};")
        isl=QHBoxLayout(self.info_strip); isl.setContentsMargins(18,0,18,0); isl.setSpacing(0)

        self._info_cells: dict[str, QLabel] = {}
        for key, val, col in [("NAME","—",C_TEXT),("DOB","—",C_TEXT_MID),("EMAIL","—",C_TEXT_MID),("MULANK","—",C_ACCENT)]:
            cell=QVBoxLayout(); cell.setSpacing(1)
            v=QLabel(val); v.setStyleSheet(f"color:{col};font-family:{FONT_MONO};font-size:13px;font-weight:700;background:transparent;")
            k=QLabel(key); k.setStyleSheet(f"color:{C_TEXT_DIM};font-family:{FONT_MONO};font-size:9px;letter-spacing:1.5px;background:transparent;")
            cell.addWidget(v,alignment=Qt.AlignmentFlag.AlignCenter); cell.addWidget(k,alignment=Qt.AlignmentFlag.AlignCenter)
            isl.addLayout(cell,1); self._info_cells[key]=v
            if key!="MULANK":
                d=QFrame(); d.setFrameShape(QFrame.Shape.VLine); d.setStyleSheet(f"color:{C_BORDER};max-width:1px;"); isl.addWidget(d)
        outer.addWidget(self.info_strip)

        # Report text
        card=make_card(14)
        cl=QVBoxLayout(card); cl.setContentsMargins(18,16,18,16); cl.setSpacing(10)

        # Word count badge
        wc_row=QHBoxLayout(); wc_row.addWidget(section_label("EVALUATION REPORT")); wc_row.addStretch()
        self._wc_lbl=QLabel("0 words")
        self._wc_lbl.setStyleSheet(f"color:{C_TEXT_MID};font-family:{FONT_MONO};font-size:10px;background:transparent;")
        wc_row.addWidget(self._wc_lbl); cl.addLayout(wc_row)

        self.report_txt=QTextEdit(); self.report_txt.setReadOnly(True); self.report_txt.setMinimumHeight(360)
        self.report_txt.setStyleSheet(f"""
            QTextEdit{{background:{C_BG};border:1.5px solid {C_BORDER};border-radius:12px;
            color:{C_TEXT};padding:16px;font-family:{FONT_MAIN};font-size:13px;
            selection-background-color:{C_ACCENT_DIM};}}
            QScrollBar:vertical{{background:{C_SURFACE};width:6px;border-radius:3px;}}
            QScrollBar::handle:vertical{{background:{C_BORDER2};border-radius:3px;min-height:30px;}}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}
        """)
        self.report_txt.setPlaceholderText("Complete the session to generate the report…")
        self.report_txt.textChanged.connect(self._update_wc)
        cl.addWidget(self.report_txt); outer.addWidget(card)

        # Buttons
        br=QHBoxLayout(); br.setSpacing(10)
        copy=QPushButton("  ⎘   Copy Report"); copy.setFixedHeight(42); copy.setCursor(Qt.CursorShape.PointingHandCursor)
        copy.setStyleSheet(f"QPushButton{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {C_ACCENT},stop:1 {C_ACCENT2});color:#020D09;border:none;border-radius:10px;font-family:{FONT_MAIN};font-size:13px;font-weight:700;}}QPushButton:hover{{background:#00F0B5;}}")
        copy.clicked.connect(lambda: (QApplication.clipboard().setText(self.report_txt.toPlainText()), copy.setText("  ✓   Copied!"), QTimer.singleShot(2000, lambda: copy.setText("  ⎘   Copy Report"))))
        clr=QPushButton("↺  Clear"); clr.setFixedHeight(42); clr.setFixedWidth(90); clr.setCursor(Qt.CursorShape.PointingHandCursor)
        clr.setStyleSheet(f"QPushButton{{background:transparent;color:{C_TEXT_MID};border:1.5px solid {C_BORDER};border-radius:10px;font-family:{FONT_MAIN};font-size:12px;font-weight:600;}}QPushButton:hover{{border-color:{C_BORDER2};color:{C_TEXT};}}")
        clr.clicked.connect(self.report_txt.clear)
        br.addWidget(copy); br.addWidget(clr); outer.addLayout(br)

    def _update_wc(self):
        words=len(self.report_txt.toPlainText().split())
        col = C_ACCENT if words<=300 else C_RED
        self._wc_lbl.setText(f"{words} / 300 words")
        self._wc_lbl.setStyleSheet(f"color:{col};font-family:{FONT_MONO};font-size:10px;background:transparent;font-weight:700;")

    def set_data(self, report, name, dob, email, mulank):
        self.report_txt.setPlainText(report)
        self._info_cells["NAME"].setText(name or "—")
        self._info_cells["DOB"].setText(dob or "—")
        self._info_cells["EMAIL"].setText(email or "—")
        self._info_cells["MULANK"].setText(str(mulank) if mulank else "—")


# ══════════════════════════════════════════════════════════
#  SETTINGS PAGE  (with language toggle stored here)
# ══════════════════════════════════════════════════════════
class SettingsPage(QWidget):
    lang_changed = pyqtSignal(str)   # "en" or "hi"

    def __init__(self):
        super().__init__()
        self._lang = "en"
        self.setStyleSheet("background:transparent;")
        self._build()

    def get_language(self) -> str:
        return self._lang

    def _divider(self):
        d=QFrame(); d.setFrameShape(QFrame.Shape.HLine)
        d.setStyleSheet(f"background:{C_BORDER};max-height:1px;border:none;"); d.setFixedHeight(1)
        return d

    def _build(self):
        outer=QVBoxLayout(self); outer.setContentsMargins(32,24,32,24); outer.setSpacing(0)
        outer.addWidget(QLabel("Settings", styleSheet=f"color:{C_TEXT};font-family:{FONT_MAIN};font-size:22px;font-weight:700;"))
        outer.addWidget(QLabel("Configure session parameters", styleSheet=f"color:{C_TEXT_DIM};font-family:{FONT_MONO};font-size:10px;letter-spacing:2px;margin-bottom:18px;"))

        scroll=QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"QScrollArea{{border:none;background:transparent;}}QScrollBar:vertical{{background:{C_SURFACE};width:6px;border-radius:3px;}}QScrollBar::handle:vertical{{background:{C_BORDER2};border-radius:3px;min-height:30px;}}QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}")
        content=QWidget(); content.setStyleSheet("background:transparent;")
        cl=QVBoxLayout(content); cl.setSpacing(14); cl.setContentsMargins(0,0,12,0)

        # ── Language Card (featured) ──────────────────────
        lang_card=make_card()
        ll=QVBoxLayout(lang_card); ll.setContentsMargins(20,18,20,20); ll.setSpacing(14)
        ll.addWidget(section_label("INTERVIEW LANGUAGE"))

        # Language selector row with live preview
        lang_row=QHBoxLayout(); lang_row.setSpacing(12)
        self._en_btn=self._lang_btn("🇬🇧  English", True)
        self._hi_btn=self._lang_btn("🇮🇳  Hindi  (हिंदी)", False)
        self._en_btn.clicked.connect(lambda: self._set_lang("en"))
        self._hi_btn.clicked.connect(lambda: self._set_lang("hi"))
        lang_row.addWidget(self._en_btn); lang_row.addWidget(self._hi_btn)
        ll.addLayout(lang_row)

        self._lang_note=QLabel("Questions and report will be in English")
        self._lang_note.setStyleSheet(f"color:{C_TEXT_MID};font-family:{FONT_MAIN};font-size:11px;background:transparent;")
        ll.addWidget(self._lang_note)
        cl.addWidget(lang_card)

        # ── Camera Card ───────────────────────────────────
        cam_card=make_card()
        cam_l=QVBoxLayout(cam_card); cam_l.setContentsMargins(20,18,20,20); cam_l.setSpacing(14)
        cam_l.addWidget(section_label("CAMERA & VIDEO"))
        for lbl,desc,w in [
            ("Camera Device","Select video input",make_combo(["Default (0)","USB (1)","Virtual"])),
            ("Resolution","Capture resolution",make_combo(["1280×720","1920×1080","640×480"])),
            ("Frame Rate","Frames per second",make_combo(["30 FPS","24 FPS","60 FPS"])),
            ("Mirror Preview","Flip feed horizontally",ToggleSwitch(True)),
            ("Scan Overlay","Animated scan line on camera",ToggleSwitch(True)),
        ]:
            cam_l.addWidget(settings_row(lbl,desc,w)); cam_l.addWidget(self._divider())
        cl.addWidget(cam_card)

        # ── Session Card ──────────────────────────────────
        ses_card=make_card()
        ses_l=QVBoxLayout(ses_card); ses_l.setContentsMargins(20,18,20,20); ses_l.setSpacing(14)
        ses_l.addWidget(section_label("SESSION"))
        for lbl,desc,w in [
            ("Question Count","Questions per session",make_combo(["5 Questions","3 Questions","7 Questions"])),
            ("Auto-listen","Auto-start listening after speak",ToggleSwitch(True)),
            ("Save Recording","Record session audio",ToggleSwitch(False)),
        ]:
            ses_l.addWidget(settings_row(lbl,desc,w)); ses_l.addWidget(self._divider())
        cl.addWidget(ses_card)

        # ── Save / Reset ──────────────────────────────────
        br=QHBoxLayout(); br.setSpacing(10)
        sv=QPushButton("  ✓   Save"); sv.setFixedHeight(42); sv.setCursor(Qt.CursorShape.PointingHandCursor)
        sv.setStyleSheet(f"QPushButton{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {C_ACCENT},stop:1 {C_ACCENT2});color:#020D09;border:none;border-radius:10px;font-family:{FONT_MAIN};font-size:13px;font-weight:700;}}QPushButton:hover{{background:#00F0B5;}}")
        rs=QPushButton("↺  Reset"); rs.setFixedHeight(42); rs.setFixedWidth(100); rs.setCursor(Qt.CursorShape.PointingHandCursor)
        rs.setStyleSheet(f"QPushButton{{background:transparent;color:{C_TEXT_MID};border:1.5px solid {C_BORDER};border-radius:10px;font-family:{FONT_MAIN};font-size:12px;font-weight:600;}}QPushButton:hover{{border-color:{C_BORDER2};color:{C_TEXT};}}")
        br.addWidget(sv); br.addWidget(rs); cl.addLayout(br); cl.addStretch()
        scroll.setWidget(content); outer.addWidget(scroll)

    def _lang_btn(self, text, active):
        b=QPushButton(text); b.setFixedHeight(44); b.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_lang_btn_style(b, active)
        return b

    def _apply_lang_btn_style(self, btn, active):
        if active:
            btn.setStyleSheet(f"QPushButton{{background:{C_ACCENT_DIM};color:{C_ACCENT};border:1.5px solid {C_ACCENT};border-radius:11px;font-family:{FONT_MAIN};font-size:13px;font-weight:700;}}QPushButton:hover{{background:{C_ACCENT_DIM};}}")
        else:
            btn.setStyleSheet(f"QPushButton{{background:{C_SURFACE};color:{C_TEXT_MID};border:1.5px solid {C_BORDER};border-radius:11px;font-family:{FONT_MAIN};font-size:13px;font-weight:500;}}QPushButton:hover{{background:{C_CARD};color:{C_TEXT};border-color:{C_BORDER2};}}")

    def _set_lang(self, lang):
        self._lang=lang
        self._apply_lang_btn_style(self._en_btn, lang=="en")
        self._apply_lang_btn_style(self._hi_btn, lang=="hi")
        if lang=="hi":
            self._lang_note.setText("प्रश्न और रिपोर्ट हिंदी में होंगे")
            self._lang_note.setStyleSheet(f"color:{C_ACCENT};font-family:{FONT_MAIN};font-size:11px;background:transparent;")
        else:
            self._lang_note.setText("Questions and report will be in English")
            self._lang_note.setStyleSheet(f"color:{C_TEXT_MID};font-family:{FONT_MAIN};font-size:11px;background:transparent;")
        self.lang_changed.emit(lang)


# ══════════════════════════════════════════════════════════
#  MAIN WINDOW
# ══════════════════════════════════════════════════════════
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PsychOS  ·  v3.1")
        self.resize(1160,720)
        self.setMinimumSize(960,640)
        self.setStyleSheet(f"background:{C_BG};")
        self._build()

    def _build(self):
        root=QHBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # ── Sidebar ───────────────────────────────────────
        sidebar=QFrame(); sidebar.setFixedWidth(200)
        sidebar.setStyleSheet(f"QFrame{{background:{C_SURFACE};border-right:1px solid {C_BORDER};}}")
        sb=QVBoxLayout(sidebar); sb.setContentsMargins(0,0,0,0); sb.setSpacing(0)

        la=QWidget(); la.setFixedHeight(72); la.setStyleSheet("background:transparent;")
        ll=QVBoxLayout(la); ll.setContentsMargins(20,18,20,14)
        ll.addWidget(QLabel("PsychOS", styleSheet=f"color:{C_TEXT};font-family:{FONT_MAIN};font-size:15px;font-weight:800;"))
        ll.addWidget(QLabel("● SYSTEM ACTIVE", styleSheet=f"color:{C_ACCENT};font-family:{FONT_MONO};font-size:9px;letter-spacing:1.5px;"))
        sb.addWidget(la)

        d=QFrame(); d.setFrameShape(QFrame.Shape.HLine); d.setFixedHeight(1)
        d.setStyleSheet(f"background:{C_BORDER};border:none;"); sb.addWidget(d); sb.addSpacing(10)
        sb.addWidget(QLabel("NAVIGATION", styleSheet=f"color:{C_TEXT_DIM};font-family:{FONT_MONO};font-size:9px;letter-spacing:2px;padding-left:20px;"))

        self.nav_s  = NavButton("◈", "Session")
        self.nav_r  = NavButton("◉", "Report")
        self.nav_cfg= NavButton("◎", "Settings")
        self.nav_s.set_active(True)
        self.nav_s.clicked.connect(lambda: self._switch(0))
        self.nav_r.clicked.connect(lambda: self._switch(1))
        self.nav_cfg.clicked.connect(lambda: self._switch(2))
        for b in [self.nav_s, self.nav_r, self.nav_cfg]: sb.addWidget(b)
        for icon,lbl in [("◍","Candidates"),("▣","History")]: sb.addWidget(NavButton(icon,lbl))

        sb.addStretch()
        d2=QFrame(); d2.setFrameShape(QFrame.Shape.HLine); d2.setFixedHeight(1)
        d2.setStyleSheet(f"background:{C_BORDER};border:none;"); sb.addWidget(d2)

        pf=QWidget(); pf.setFixedHeight(60); pf.setStyleSheet("background:transparent;")
        pfl=QHBoxLayout(pf); pfl.setContentsMargins(16,10,16,10)
        av=QLabel("AI"); av.setFixedSize(32,32); av.setAlignment(Qt.AlignmentFlag.AlignCenter)
        av.setStyleSheet(f"background:{C_ACCENT_DIM};color:{C_ACCENT};border-radius:16px;font-family:{FONT_MAIN};font-size:11px;font-weight:700;border:1px solid {C_ACCENT}44;")
        pfl.addWidget(av); pfl.addSpacing(8)
        pfl.addWidget(QLabel("Admin", styleSheet=f"color:{C_TEXT_MID};font-family:{FONT_MAIN};font-size:12px;background:transparent;"))
        sb.addWidget(pf)

        # ── Pages ─────────────────────────────────────────
        self.stack=QStackedWidget(); self.stack.setStyleSheet("background:transparent;")
        self.settings_page = SettingsPage()
        self.session_page  = SessionPage(OAI_KEY, self.settings_page)
        self.report_page   = ReportPage()

        self.stack.addWidget(self.session_page)   # 0
        self.stack.addWidget(self.report_page)    # 1
        self.stack.addWidget(self.settings_page)  # 2

        root.addWidget(sidebar); root.addWidget(self.stack)

    def _switch(self, idx):
        self.stack.setCurrentIndex(idx)
        self.nav_s.set_active(idx==0)
        self.nav_r.set_active(idx==1)
        self.nav_cfg.set_active(idx==2)

    def show_report(self, report, name, dob, email, mulank):
        self.report_page.set_data(report, name, dob, email, mulank)
        self._switch(1)

    def closeEvent(self, e):
        self.session_page.release(); e.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())