import "./App.css";
import Recorder from "./Recorder";

// ── Tooth icon used in the navbar ────────────────────────────────────────────
function ToothLogo({ className }) {
  return (
    <svg
      viewBox="0 0 32 32"
      className={className}
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id="navLogoGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#a5f3fc" />
          <stop offset="100%" stopColor="#06b6d4" />
        </linearGradient>
      </defs>
      <path
        d="M 16 3 C 9 3 4 7 4 14 C 4 19 6 23 8 27 C 9 29 11 30 12 27
           C 13 24 14 22 16 22 C 18 22 19 24 20 27 C 21 30 23 29 24 27
           C 26 23 28 19 28 14 C 28 7 23 3 16 3 Z"
        fill="url(#navLogoGrad)"
        stroke="rgba(255,255,255,0.45)"
        strokeWidth="0.8"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// ── Big levitating tooth used in the hero ───────────────────────────────────
function FloatingTooth() {
  return (
    <div className="tooth-stage" aria-hidden="true">
      <div className="tooth-halo" />
      <span className="tooth-sparkle tooth-sparkle--1">✦</span>
      <span className="tooth-sparkle tooth-sparkle--2">✦</span>
      <span className="tooth-sparkle tooth-sparkle--3">✧</span>
      <span className="tooth-sparkle tooth-sparkle--4">✦</span>
      <svg
        viewBox="0 0 200 240"
        className="tooth-svg"
        xmlns="http://www.w3.org/2000/svg"
      >
        <defs>
          <linearGradient id="toothBody" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#ffffff" />
            <stop offset="55%" stopColor="#dff6ff" />
            <stop offset="100%" stopColor="#9ec9dc" />
          </linearGradient>
          <radialGradient id="toothShine" cx="35%" cy="28%" r="35%">
            <stop offset="0%" stopColor="rgba(255,255,255,0.95)" />
            <stop offset="100%" stopColor="rgba(255,255,255,0)" />
          </radialGradient>
          <linearGradient id="toothEdge" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#67e8f9" />
            <stop offset="100%" stopColor="#0e7490" />
          </linearGradient>
        </defs>
        <path
          d="M 100 14
             C 60 14, 26 32, 26 92
             C 26 132, 36 162, 52 202
             C 62 226, 78 234, 88 214
             C 94 196, 96 178, 100 178
             C 104 178, 106 196, 112 214
             C 122 234, 138 226, 148 202
             C 164 162, 174 132, 174 92
             C 174 32, 140 14, 100 14 Z"
          fill="url(#toothBody)"
          stroke="url(#toothEdge)"
          strokeWidth="2.4"
          strokeLinejoin="round"
        />
        <ellipse cx="74" cy="58" rx="22" ry="40" fill="url(#toothShine)" />
        <path
          d="M 50 96 Q 100 116, 150 96"
          stroke="rgba(14,116,144,0.28)"
          strokeWidth="2"
          fill="none"
        />
      </svg>
      <div className="tooth-shadow" />
    </div>
  );
}

function App() {
  return (
    <div className="page">
      {/* Animated background orbs */}
      <div className="bg-orbs" aria-hidden="true">
        <span className="orb orb--1" />
        <span className="orb orb--2" />
        <span className="orb orb--3" />
      </div>

      {/* Top navigation */}
      <nav className="navbar">
        <div className="navbar__inner">
          <div className="navbar__brand">
            <ToothLogo className="navbar__logo" />
            <span className="navbar__title">
              Seraph
              <span className="navbar__title-accent"> · Medical Profile Assistant</span>
            </span>
          </div>
          <div className="navbar__status">
            <span className="navbar__pill">
              <span className="navbar__pill-dot" /> AI Online
            </span>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <header className="hero">
        <div className="hero__copy">
          <span className="hero__kicker">USJ · Faculty of Dental Medicine</span>
          <h1 className="hero__title">
            <span className="hero__title-accent">Just speak.</span>
          </h1>
          <p className="hero__lede">
            An intelligent voice assistant for the Seraph dental platform —
            query patient data, update records, and capture treatments through
            natural conversation, with every action logged for traceability.
          </p>
          <div className="hero__chips">
            <span className="hero__chip">
              <span className="hero__chip-dot" /> Whisper STT
            </span>
            <span className="hero__chip">
              <span className="hero__chip-dot hero__chip-dot--alt" /> Qwen 1.7 NLP
            </span>
            <span className="hero__chip">
              <span className="hero__chip-dot hero__chip-dot--ok" /> Audit-logged
            </span>
          </div>
        </div>

        <div className="hero__art">
          <FloatingTooth />
        </div>
      </header>

      {/* The original Recorder — untouched */}
      <Recorder />

      <footer className="site-footer">
        <span>Medical Profile Assistant &amp; System Advisor · FYP_26_09</span>
        <span className="site-footer__sep">·</span>
        <span>USJ × Tomorrow Services</span>
        <span className="site-footer__sep">·</span>
        <span>Powered by Whisper × Qwen 1.7 × FastAPI</span>
      </footer>
    </div>
  );
}

export default App;
