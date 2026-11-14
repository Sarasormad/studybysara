/* StudyBySara "game feel": instant feedback, celebrations and sound.
 *
 * Everything here is an enhancement. Without JavaScript the app works the same, just
 * with page reloads and no effects. Motion is skipped for people who ask their device
 * for reduced motion, sound is OFF until the student turns it on, and nothing flashes.
 */
(() => {
  "use strict";
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---------------------------------------------------------------- Sound
  // Tiny tones made with the Web Audio API, so there are no sound files to load.
  const Sound = {
    ctx: null,
    enabled() {
      try { return localStorage.getItem("sb-sound") === "on"; } catch { return false; }
    },
    setEnabled(on) {
      try { localStorage.setItem("sb-sound", on ? "on" : "off"); } catch { /* private mode */ }
    },
    context() {
      if (!this.ctx) {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return null;
        this.ctx = new Ctx();
      }
      return this.ctx;
    },
    tone(freq, start, dur, type = "sine", volume = 0.12) {
      const c = this.ctx, t = c.currentTime + start;
      const osc = c.createOscillator(), gain = c.createGain();
      osc.type = type;
      osc.frequency.setValueAtTime(freq, t);
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(volume, t + 0.012);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      osc.connect(gain).connect(c.destination);
      osc.start(t);
      osc.stop(t + dur + 0.05);
    },
    SOUNDS: {
      correct: [[660, 0, 0.12], [990, 0.09, 0.2]],
      wrong: [[260, 0, 0.16, "triangle", 0.07], [196, 0.12, 0.22, "triangle", 0.06]],
      combo: [[523, 0, 0.1], [659, 0.08, 0.1], [784, 0.16, 0.1], [1047, 0.24, 0.25]],
      complete: [[523, 0, 0.14], [659, 0.12, 0.14], [784, 0.24, 0.14], [1047, 0.36, 0.4], [1319, 0.36, 0.4, "sine", 0.06]],
      coin: [[1319, 0, 0.08, "square", 0.04], [1760, 0.07, 0.14, "square", 0.04]],
    },
    async play(name) {
      if (!this.enabled()) return;
      const c = this.context();
      if (!c) return;
      if (c.state === "suspended") {
        try { await c.resume(); } catch { return; }
      }
      if (c.state !== "running") return;          // browser blocked audio until a tap: stay quiet
      (this.SOUNDS[name] || []).forEach((t) => this.tone(...t));
    },
  };

  function setupSoundToggle() {
    const btn = document.querySelector("[data-sound-toggle]");
    if (!btn) return;
    const paint = () => {
      const on = Sound.enabled();
      btn.textContent = on ? "🔊" : "🔇";
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.title = on ? "Sound on (click to mute)" : "Sound off (click to turn on)";
    };
    paint();
    btn.hidden = false;
    btn.addEventListener("click", () => {
      Sound.setEnabled(!Sound.enabled());
      paint();
      if (Sound.enabled()) { Sound.context()?.resume(); Sound.play("coin"); }
    });
    // Browsers only allow audio after a tap; wake the audio engine on the first one.
    document.addEventListener("pointerdown", () => { if (Sound.enabled()) Sound.context()?.resume(); }, { once: true });
  }

  // ---------------------------------------------------------------- Effects
  function countUp(el, from, to, ms = 700) {
    if (!el) return;
    if (reduceMotion || from === to) { el.textContent = to; return; }
    const start = performance.now();
    const step = (now) => {
      const k = Math.min(1, (now - start) / ms);
      const eased = 1 - Math.pow(1 - k, 3);
      el.textContent = Math.round(from + (to - from) * eased);
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function floatText(anchor, text, kind = "points") {
    if (!anchor || reduceMotion) return;
    const r = anchor.getBoundingClientRect();
    const el = document.createElement("span");
    el.className = `float-text float-${kind}`;
    el.textContent = text;
    el.setAttribute("aria-hidden", "true");
    el.style.left = `${r.left + r.width / 2 + window.scrollX}px`;
    el.style.top = `${r.top + window.scrollY}px`;
    document.body.appendChild(el);
    el.addEventListener("animationend", () => el.remove());
  }

  const CONFETTI_COLORS = ["#6d28d9", "#ec4899", "#f59e0b", "#14b8a6", "#3b82f6", "#facc15"];
  function confetti(pieces = 90) {
    if (reduceMotion) return;
    const layer = document.createElement("div");
    layer.className = "confetti-layer";
    layer.setAttribute("aria-hidden", "true");
    for (let i = 0; i < pieces; i++) {
      const p = document.createElement("i");
      p.style.left = `${Math.random() * 100}vw`;
      p.style.background = CONFETTI_COLORS[i % CONFETTI_COLORS.length];
      p.style.setProperty("--drift", `${(Math.random() - 0.5) * 220}px`);
      p.style.setProperty("--spin", `${(Math.random() - 0.5) * 1080}deg`);
      p.style.animationDuration = `${1.6 + Math.random() * 1.4}s`;
      p.style.animationDelay = `${Math.random() * 0.35}s`;
      if (i % 3 === 0) p.style.borderRadius = "50%";
      layer.appendChild(p);
    }
    document.body.appendChild(layer);
    setTimeout(() => layer.remove(), 3600);
  }

  function updateWallet(newValue) {
    const el = document.querySelector("[data-wallet-value]");
    if (!el || newValue == null) return;
    const old = parseInt(el.textContent, 10) || 0;
    if (newValue > old) {
      const pill = el.closest(".wallet-pill");
      pill?.classList.remove("wallet-bump");
      void pill?.offsetWidth;                       // restart the bump animation
      pill?.classList.add("wallet-bump");
    }
    countUp(el, old, newValue, 900);
  }

  // What to celebrate on a freshly shown feedback panel.
  function celebrate(panel, data) {
    const correct = data.correct;
    const combo = data.combo || 0;
    if (correct) {
      const pill = panel.querySelector(".points-pill") || panel.querySelector(".verdict-title");
      if (data.points) floatText(pill, `+${data.points}`);
      if (combo >= 3 && (combo === 3 || combo % 5 === 0)) {
        Sound.play("combo");
        confetti(40);
      } else {
        Sound.play("correct");
      }
    } else {
      Sound.play("wrong");
    }
    if (data.streak_message) {
      confetti(110);
      setTimeout(() => Sound.play("complete"), 350);
    }
    updateWallet(data.wallet);
  }

  // ---------------------------------------------------------------- Instant answers
  function setupInstantAnswers() {
    document.addEventListener("submit", async (event) => {
      const form = event.target;
      if (!form.matches(".answer-form")) return;
      event.preventDefault();
      const button = form.querySelector('button[type="submit"]');
      if (button) { button.disabled = true; button.classList.add("is-checking"); }
      let response, data;
      try {
        response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          headers: { "X-Requested-With": "fetch", Accept: "application/json" },
          credentials: "same-origin",
        });
        data = await response.json();
      } catch {
        form.submit();                              // network or server hiccup: fall back to a normal post
        return;
      }
      if (!response.ok) {
        showFormError(form, data.error || "Something went wrong. Try again.");
        if (button) { button.disabled = false; button.classList.remove("is-checking"); }
        return;
      }
      const oldPanel = form.closest(".question-panel");
      const holder = document.createElement("div");
      holder.innerHTML = data.html.trim();
      const panel = holder.firstElementChild;
      oldPanel.replaceWith(panel);
      if (data.today_html) {
        const today = document.querySelector(".practice-top .today");
        const fresh = document.createElement("div");
        fresh.innerHTML = data.today_html.trim();
        if (today && fresh.firstElementChild) today.replaceWith(fresh.firstElementChild);
      }
      if (data.feedback_url) history.replaceState(null, "", data.feedback_url);   // refresh shows this feedback
      panel.querySelector(".next-btn")?.focus({ preventScroll: true });
      celebrate(panel, data);
    });
  }

  function showFormError(form, message) {
    let note = form.querySelector(".form-error");
    if (!note) {
      note = document.createElement("p");
      note.className = "flash flash-error form-error";
      note.setAttribute("role", "alert");
      form.prepend(note);
    }
    note.textContent = message;
  }

  // ---------------------------------------------------------------- Page-load effects
  function onLoad() {
    // Results screen: count the points up and celebrate big wins.
    document.querySelectorAll("[data-countup]").forEach((el) => countUp(el, 0, parseInt(el.dataset.countup, 10) || 0, 1100));
    if (document.querySelector("[data-confetti]")) setTimeout(() => confetti(120), 250);
    const soundEl = document.querySelector("[data-sound]");
    if (soundEl) Sound.play(soundEl.dataset.sound);
  }

  setupSoundToggle();
  setupInstantAnswers();
  onLoad();
  window.StudyBySaraGame = { confetti, floatText, countUp, Sound };   // handy for experimenting in the console
})();
