"""El hareketi motoru testleri: sentetik eller + MediaPipe'ın gerçek referans iskeletleri."""

import json
import math
import random
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

from brain.gestures import GestureEngine, GestureParams, hand_metrics  # noqa: E402
from brain.hand_actions import HandGraphController  # noqa: E402
from brain.view import GraphView  # noqa: E402
from synthetic_hands import frame, hand  # noqa: E402

DT = 1 / 24


def run(engine, frames, t0=0.0):
    """frames: [[lm...], ...] her biri bir kare; tüm olayları sırayla döndürür."""
    events, t = [], t0
    for f in frames:
        out = engine.update(f, t, 4 / 3)
        events.extend(out["events"])
        t += DT
    return events, t


def kinds(events):
    return [e[0] for e in events if e[0] != "hand_lost"]


class RealLandmarkPoses(unittest.TestCase):
    """MediaPipe test verisindeki gerçek el iskeletleri (Apache-2.0) doğru sınıflanmalı."""

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((HERE / "data" / "mediapipe_real_landmarks.json").read_text())

    @staticmethod
    def data_fist():
        return json.loads((HERE / "data" / "mediapipe_real_landmarks.json").read_text())["fist"]

    def test_real_pose_classification(self):
        expect = {"victory": "peace", "fist": "fist", "pointing_up": "point",
                  "expected_left_up_hand_landmarks": "open", "expected_right_up_hand_landmarks": "open",
                  "expected_left_down_hand_landmarks": "open", "expected_right_down_hand_landmarks": "open"}
        for aspect in (0.75, 1.0, 4 / 3, 16 / 9):
            for name, pose in expect.items():
                with self.subTest(name=name, aspect=aspect):
                    self.assertEqual(hand_metrics(self.data[name], aspect)["pose"], pose)

    def test_real_fist_is_not_a_pinch(self):
        # Gerçek yumrukta başparmak-işaret uç mesafesi sıkıştırma eşiğinin altında (≈0.2);
        # işaret parmağı katlı olduğu için yine de sıkıştırma sayılmamalı.
        m = hand_metrics(self.data["fist"], 4 / 3)
        self.assertLess(m["pinch"], GestureParams().pinch_in)
        self.assertFalse(m["pinch_ok"])
        engine = GestureEngine()
        events, _ = run(engine, [[{"lm": self.data["fist"], "score": 1.0}]] * 20)
        self.assertNotIn("press", kinds(events))

    def test_real_victory_triggers_reset_only_after_hold(self):
        engine = GestureEngine(GestureParams(reset_gesture=True))
        f = [{"lm": self.data["victory"], "score": 1.0}]
        events, _ = run(engine, [f] * int(0.8 / DT))
        self.assertNotIn("reset", kinds(events))
        events, _ = run(engine, [f] * int(0.5 / DT), t0=0.8)
        self.assertEqual(kinds(events).count("reset"), 1)


class PinchAndGrab(unittest.TestCase):
    def test_arming_and_earning_frames(self):
        engine = GestureEngine()
        p = engine.p
        events, _ = run(engine, [frame(hand("pinch"))] * (p.arm_frames + p.pinch_earn - 2))
        self.assertNotIn("press", kinds(events), "el hazırlanmadan/eşik kazanılmadan tutuş başlamamalı")
        events, _ = run(engine, [frame(hand("pinch"))] * 2, t0=1.0)
        self.assertIn("press", kinds(events))

    def test_single_frame_glitch_is_ignored(self):
        engine = GestureEngine()
        seq = [frame(hand("open"))] * 6
        for _ in range(5):
            seq += [frame(hand("pinch"))] + [frame(hand("open"))] * 3   # tek karelik yanlış sıkıştırma
        events, _ = run(engine, seq)
        self.assertNotIn("press", kinds(events))

    def test_hysteresis_prevents_flapping(self):
        engine = GestureEngine()
        seq = [frame(hand("open"))] * 5 + [frame(hand("pinch"))] * 4
        # eşikler arasında (0.30 < oran < 0.46) titreşim: bırakma OLMAMALI
        for k in range(30):
            gap = 0.33 if k % 2 else 0.42
            seq.append(frame(hand("halfpinch", pinch_gap=gap)))
        events, t = run(engine, seq)
        self.assertEqual(kinds(events).count("press"), 1)
        self.assertNotIn("release", kinds(events))
        events, _ = run(engine, [frame(hand("open"))] * 3, t0=t)
        self.assertIn("release", kinds(events))

    def test_small_or_far_hand_never_arms(self):
        engine = GestureEngine()
        events, _ = run(engine, [frame(hand("pinch", size=0.03))] * 30)
        self.assertEqual(kinds(events), [])

    def test_low_confidence_hand_ignored(self):
        # "score" = MediaPipe sağ/sol el güveni (görülme güveni MediaPipe içinde ayrıca eşiklenir). 04.10'dan beri
        # yalnız çok düşük (<0,20) olan atılır; 0,3-0,5'lik ikinci eller artık izlenir (SecondHandAndSingleFist).
        engine = GestureEngine()
        events, _ = run(engine, [frame(hand("pinch"), score=0.1)] * 30)
        self.assertEqual(kinds(events), [])

    def test_tracking_loss_releases_without_action(self):
        engine = GestureEngine()
        events, t = run(engine, [frame(hand("open"))] * 5 + [frame(hand("pinch"))] * 6)
        self.assertIn("press", kinds(events))
        events, _ = run(engine, [[]] * 12, t0=t)
        rel = [e for e in events if e[0] == "release"]
        self.assertEqual(len(rel), 1)
        self.assertEqual(rel[0][-1], "lost")


class GraphInteraction(unittest.TestCase):
    def make(self):
        view = GraphView({"a": (0.0, 0.0), "b": (300.0, 0.0)}, 1000, 750)
        view.radius = {"a": 8.0, "b": 8.0}
        view.fit()
        return view, HandGraphController(view), GestureEngine()

    def test_pinch_on_node_grabs_and_moves_it(self):
        view, ctrl, engine = self.make()
        ax, ay = view.node_screen("a")
        # imleci AÇIK elle düğümün üstüne getir (nişan alınan nokta = açık eldeki başparmak/işaret ucu
        # ortası; sıkıştırınca imleç kaymaz), AMP eşlemesinin tersi
        nx, ny = ax / view.width, ay / view.height
        a = engine.p.amp
        raw = (0.5 + (nx - 0.5) / a, 0.5 + (ny - 0.5) / a)
        m = hand_metrics(hand("open"), 4 / 3)
        dx, dy = raw[0] - m["cursor"][0], raw[1] - m["cursor"][1]
        base_cx, base_cy = 0.5 + dx, 0.55 + dy
        seq = [frame(hand("open", cx=base_cx, cy=base_cy))] * 5
        seq += [frame(hand("pinch", cx=base_cx, cy=base_cy))] * 5
        seq += [frame(hand("pinch", cx=base_cx + 0.01 * k, cy=base_cy)) for k in range(1, 15)]
        seq += [frame(hand("open", cx=base_cx + 0.14, cy=base_cy))] * 4
        moved, selected = set(), None
        t = 0.0
        for f in seq:
            ch = ctrl.apply(engine.update(f, t, 4 / 3)["events"])
            moved |= ch["moved"]
            selected = selected or ch["selected"]
            t += DT
        self.assertEqual(selected, "a")
        self.assertEqual(moved, {"a"})
        self.assertGreater(view.pos["a"][0], 50.0, "düğüm sağa taşınmalı")
        self.assertEqual(view.pos["b"], (300.0, 0.0), "diğer düğüm yerinde kalmalı")
        self.assertEqual(view.moved_nodes(), ["a"])

    def test_pinch_on_empty_space_pans(self):
        view, ctrl, engine = self.make()
        before = (view.ox, view.oy)
        seq = [frame(hand("open", cx=0.2, cy=0.2))] * 5 + [frame(hand("pinch", cx=0.2, cy=0.2))] * 5
        seq += [frame(hand("pinch", cx=0.2 + 0.01 * k, cy=0.2)) for k in range(1, 12)]
        t = 0.0
        for f in seq:
            ctrl.apply(engine.update(f, t, 4 / 3)["events"])
            t += DT
        self.assertGreater(view.ox, before[0])
        self.assertEqual(view.moved_nodes(), [])

    def test_two_hand_zoom_in_and_out(self):
        view, ctrl, engine = self.make()
        s0 = view.scale
        left = lambda x: hand("pinch", cx=x, cy=0.5)     # noqa: E731
        right = lambda x: hand("pinch", cx=x, cy=0.5)    # noqa: E731
        seq = [frame(hand("open", cx=0.35), hand("open", cx=0.65))] * 5
        seq += [frame(left(0.35), right(0.65))] * 5
        seq += [frame(left(0.35 - 0.01 * k), right(0.65 + 0.01 * k)) for k in range(1, 16)]
        t, evs = 0.0, []
        for f in seq:
            out = engine.update(f, t, 4 / 3)
            evs += out["events"]
            ctrl.apply(out["events"])
            t += DT
        self.assertIn("zoom_begin", kinds(evs))
        self.assertNotIn("press", kinds(evs), "iki el sıkıştırmada tek el tutuşu başlamamalı")
        self.assertGreater(view.scale, s0 * 1.3)
        # ellerini birbirine yaklaştır → uzaklaş
        s1 = view.scale
        for k in range(1, 20):
            ctrl.apply(engine.update(frame(left(0.20 + 0.012 * k), right(0.80 - 0.012 * k)), t, 4 / 3)["events"])
            t += DT
        self.assertLess(view.scale, s1)
        # bir el bırakınca yakınlaştırma biter; diğer el bırakmadan tutuş/kaydırma başlatmaz
        evs = []
        for _ in range(6):
            out = engine.update(frame(hand("open", cx=0.4, cy=0.5), right(0.6)), t, 4 / 3)
            evs += out["events"]
            ctrl.apply(out["events"])
            t += DT
        self.assertIn("zoom_end", kinds(evs))
        self.assertNotIn("press", kinds(evs))

    def test_zoom_deadzone_ignores_jitter(self):
        view, ctrl, engine = self.make()
        seq = [frame(hand("open", cx=0.35), hand("open", cx=0.65))] * 5
        seq += [frame(hand("pinch", cx=0.35), hand("pinch", cx=0.65))] * 5
        for k in range(40):  # ±%1 titreşim
            j = 0.003 * (1 if k % 2 else -1)
            seq.append(frame(hand("pinch", cx=0.35 - j), hand("pinch", cx=0.65 + j)))
        s0 = view.scale
        evs, _ = run(engine, seq)
        ctrl.apply(evs)
        self.assertNotIn("zoom", kinds(evs))
        self.assertAlmostEqual(view.scale, s0)

    def test_hand_identity_survives_order_swap(self):
        engine = GestureEngine()
        a, b = hand("open", cx=0.3), hand("open", cx=0.7)
        t = 0.0
        for k in range(12):
            f = frame(a, b) if k % 2 else frame(b, a)   # MediaPipe sırası karede değişebilir
            out = engine.update(f, t, 4 / 3)
            t += DT
        xs = {h["slot"]: h["cursor"][0] for h in out["hands"]}
        self.assertEqual(len(xs), 2)
        # Kimlik karışsaydı filtre iki eli ortada birleştirirdi; eller ayrı kalmalı.
        self.assertGreater(abs(xs[0] - xs[1]), 0.3)
        # yuvalar yer değiştirmemeli: her yuvanın imleci tek tarafta kalır
        first = dict(xs)
        for k in range(6):
            f = frame(a, b) if k % 2 else frame(b, a)
            out = engine.update(f, t, 4 / 3)
            t += DT
        for h in out["hands"]:
            self.assertAlmostEqual(h["cursor"][0], first[h["slot"]], delta=0.05)


class CursorQuality(unittest.TestCase):
    """İmleç: sıkıştırırken kaymaz, dururken titremez, hızlı harekette geride kalmaz."""
    W, H = 1300, 800

    def run_engine(self, params, seq):
        engine, out, t = GestureEngine(params), [], 0.0
        for lm in seq:
            hands = engine.update(frame(lm), t, 4 / 3)["hands"]
            out.append(hands[0]["cursor"] if hands else None)
            t += 1 / 30
        return out

    def px(self, a, b):
        return math.hypot((a[0] - b[0]) * self.W, (a[1] - b[1]) * self.H)

    def test_pinching_does_not_move_the_cursor(self):
        seq = [hand("open", cx=0.5, cy=0.5)] * 15 + [hand("pinch", cx=0.5, cy=0.5)] * 15
        new = self.run_engine(GestureParams(), seq)
        old = self.run_engine(GestureParams(stable_anchor=False), seq)
        self.assertLess(self.px(new[-1], new[14]), 2.0)
        self.assertGreater(self.px(old[-1], old[14]), 30.0, "karşılaştırma: eski yöntemde imleç kayıyordu")

    def test_release_does_not_move_the_cursor(self):
        seq = [hand("open", cx=0.5, cy=0.5)] * 10 + [hand("pinch", cx=0.5, cy=0.5)] * 10
        seq += [hand("open", cx=0.5, cy=0.5)] * 3
        out = self.run_engine(GestureParams(), seq)
        self.assertLess(self.px(out[-1], out[19]), 2.0)

    def test_still_hand_jitter_is_suppressed(self):
        rng = random.Random(7)
        base = hand("open", cx=0.5, cy=0.5)
        seq = [[[x + rng.gauss(0, 0.002), y + rng.gauss(0, 0.002), z] for x, y, z in base] for _ in range(90)]
        out = self.run_engine(GestureParams(), seq)[30:]
        spread = max(self.px(a, b) for a in out for b in out)
        raw = [[(lm[4][0] + lm[8][0]) / 2, (lm[4][1] + lm[8][1]) / 2] for lm in seq[30:]]
        raw_spread = max(self.px(a, b) for a in raw for b in raw) * GestureParams().amp
        self.assertLess(spread, raw_spread / 3)

    def test_fast_motion_has_low_lag(self):
        v = 0.5  # görüntü genişliği/sn
        seq = [hand("open", cx=0.3 + v * k / 30, cy=0.5) for k in range(40)]
        truth = self.run_engine(GestureParams(min_cutoff=1e6, beta=0.0, deadband=0.0), seq)
        new = self.run_engine(GestureParams(), seq)
        old = self.run_engine(GestureParams(min_cutoff=1.2, beta=0.8, stable_anchor=False, deadband=0.0), seq)
        old_truth = self.run_engine(GestureParams(min_cutoff=1e6, beta=0.0, deadband=0.0, stable_anchor=False), seq)
        lag_new = sum(self.px(a, b) for a, b in zip(new[15:], truth[15:])) / 25
        lag_old = sum(self.px(a, b) for a, b in zip(old[15:], old_truth[15:])) / 25
        self.assertLess(lag_new, 20.0)
        self.assertLess(lag_new, lag_old / 2)

    def test_depth_separates_fingers_that_overlap_on_screen(self):
        lm = hand("open", cx=0.5, cy=0.5)
        lm[4] = [lm[8][0], lm[8][1] + 0.005, -0.08]   # başparmak ucu işaretin hemen "arkasında"
        lm[8] = [lm[8][0], lm[8][1], 0.0]
        self.assertGreater(hand_metrics(lm, 4 / 3)["pinch"], GestureParams().pinch_in)

    def test_hud_reports_quality_inputs(self):
        engine = GestureEngine()
        h = engine.update(frame(hand("open", cx=0.5, cy=0.5)), 0.0, 4 / 3)["hands"][0]
        for key in ("closeness", "raw", "score", "span", "edge", "in_zone"):
            self.assertIn(key, h)
        self.assertTrue(h["in_zone"])
        far = GestureEngine().update(frame(hand("open", cx=0.04, cy=0.5)), 0.0, 4 / 3)["hands"][0]
        self.assertFalse(far["in_zone"])
        self.assertLess(far["edge"], 0.03)
        near = GestureEngine().update(frame(hand("halfpinch", cx=0.5, cy=0.5, pinch_gap=0.12)), 0.0, 4 / 3)
        self.assertGreater(near["hands"][0]["closeness"], h["closeness"])


class ResetGesture(unittest.TestCase):
    def test_hand_reset_is_on_by_default(self):
        # 04.10.2026 kullanıcı isteği: ✌ ile sıfırlama açık (önceden kapalıydı). ~1 sn tutunca bir kez sıfırlar.
        engine = GestureEngine()
        events, _ = run(engine, [frame(hand("peace"))] * int(3.0 / DT))
        self.assertEqual(kinds(events).count("reset"), 1)
        f = [{"lm": json.loads((HERE / "data" / "mediapipe_real_landmarks.json").read_text())["victory"],
              "score": 1.0}] if (HERE / "data" / "mediapipe_real_landmarks.json").exists() else frame(hand("peace"))
        events, _ = run(GestureEngine(), [f] * int(3.0 / DT))
        self.assertEqual(kinds(events).count("reset"), 1)

    def test_short_or_other_poses_do_not_reset(self):
        events, _ = run(GestureEngine(), [frame(hand("peace"))] * int(0.6 / DT))      # yarım saniye ✌
        self.assertNotIn("reset", kinds(events))
        for pose in ("open", "fist"):
            events, _ = run(GestureEngine(), [frame(hand(pose))] * int(3.0 / DT))
            self.assertNotIn("reset", kinds(events), pose)

    def test_reset_requires_full_hold_and_fires_once(self):
        engine = GestureEngine(GestureParams(reset_gesture=True))
        events, t = run(engine, [frame(hand("peace"))] * int(0.7 / DT))
        self.assertNotIn("reset", kinds(events))
        self.assertGreater(engine.reset_progress, 0.3)
        events, t = run(engine, [frame(hand("peace"))] * int(2.5 / DT), t0=t)
        self.assertEqual(kinds(events).count("reset"), 1, "tutmaya devam etmek ikinci kez sıfırlamamalı")

    def test_reset_flicker_restarts_timer(self):
        engine = GestureEngine(GestureParams(reset_gesture=True))
        seq = []
        for _ in range(6):   # 0.5 sn ✌, 0.3 sn başka poz
            seq += [frame(hand("peace"))] * 12 + [frame(hand("open"))] * 7
        events, _ = run(engine, seq)
        self.assertNotIn("reset", kinds(events))

    def test_reset_requires_still_hand(self):
        engine = GestureEngine(GestureParams(reset_gesture=True))
        seq = [frame(hand("peace", cx=0.3 + 0.012 * k)) for k in range(60)]
        events, _ = run(engine, seq)
        self.assertNotIn("reset", kinds(events))

    def test_reset_not_during_grab(self):
        engine = GestureEngine(GestureParams(reset_gesture=True))
        seq = [frame(hand("open", cx=0.3), hand("open", cx=0.7))] * 5
        seq += [frame(hand("pinch", cx=0.3), hand("peace", cx=0.7))] * 50
        events, _ = run(engine, seq)
        self.assertIn("press", kinds(events))
        self.assertNotIn("reset", kinds(events))

    def test_reset_restores_moved_nodes_and_view(self):
        view = GraphView({"a": (0.0, 0.0), "b": (100.0, 50.0)}, 800, 600)
        view.fit()
        fitted = (view.scale, view.ox, view.oy)
        view.move_node_screen("a", 10, 10)
        view.zoom_at(2.0, 400, 300)
        ctrl = HandGraphController(view)
        ch = ctrl.apply([("reset",)])
        self.assertTrue(ch["reset"])
        self.assertEqual(view.pos, view.base)
        for got, want in zip((view.scale, view.ox, view.oy), fitted):
            self.assertAlmostEqual(got, want)


class DoubleFistUnzoom(unittest.TestCase):
    """Tek elle iki kez hızlı yumruk = yakınlaştırmadan çık (kullanıcı isteği); sıfırlamaz."""

    O = frame(hand("open"))
    F = frame(hand("fist"))

    def seq(self, fist=3, gap=3, second=3):
        return [self.O] * 5 + [self.F] * fist + [self.O] * gap + [self.F] * second + [self.O] * 3

    def test_quick_double_fist_fires_once(self):
        events, _ = run(GestureEngine(), self.seq())
        self.assertEqual(kinds(events).count("unzoom"), 1)
        self.assertNotIn("reset", kinds(events))
        self.assertNotIn("press", kinds(events))

    def test_real_mediapipe_fist_works(self):
        real = [{"lm": RealLandmarkPoses.data_fist(), "score": 1.0}]
        events, _ = run(GestureEngine(), [self.O] * 5 + [real] * 3 + [self.O] * 3 + [real] * 3)
        self.assertEqual(kinds(events).count("unzoom"), 1)

    def test_single_fist_or_long_hold_does_not_fire(self):
        events, _ = run(GestureEngine(), [self.O] * 5 + [self.F] * 40 + [self.O] * 10)
        self.assertNotIn("unzoom", kinds(events))

    def test_slow_double_fist_does_not_fire(self):
        events, _ = run(GestureEngine(), self.seq(gap=int(1.4 / DT)))
        self.assertNotIn("unzoom", kinds(events))

    def test_one_frame_flicker_is_not_a_fist(self):
        flicker = [self.O] * 5 + ([self.F] + [self.O] * 2) * 6
        events, _ = run(GestureEngine(), flicker)
        self.assertNotIn("unzoom", kinds(events))

    def test_can_be_disabled(self):
        events, _ = run(GestureEngine(GestureParams(double_fist=False)), self.seq())
        self.assertNotIn("unzoom", kinds(events))

    def test_cooldown_prevents_a_second_trigger_right_away(self):
        engine = GestureEngine()
        events, t = run(engine, self.seq())
        more, t = run(engine, [self.F] * 3 + [self.O] * 3 + [self.F] * 3, t0=t)
        self.assertEqual(kinds(events + more).count("unzoom"), 1)
        later, _ = run(engine, [self.O] * 40 + self.seq(), t0=t)
        self.assertEqual(kinds(later).count("unzoom"), 1)

    def test_not_during_two_hand_zoom_or_while_holding(self):
        a, b = hand("pinch", cx=0.3), hand("pinch", cx=0.7)
        engine = GestureEngine()
        run(engine, [frame(hand("open", cx=0.3), hand("open", cx=0.7))] * 5 + [frame(a, b)] * 5)
        self.assertEqual(engine.mode, "zoom")
        events, _ = run(engine, [frame(a, hand("fist", cx=0.7))] * 3 + [frame(a, hand("open", cx=0.7))] * 3
                        + [frame(a, hand("fist", cx=0.7))] * 3, t0=1.0)
        self.assertNotIn("unzoom", kinds(events))

    def test_opening_after_a_fist_never_becomes_a_pinch_or_double_pinch(self):
        # Yumruktan açılırken başparmak-işaret bir an yakın görünebilir; bu tutuş/odak başlatmamalı.
        P = frame(hand("pinch"))
        seq = [self.O] * 5 + [self.F] * 3 + [P] * 4 + [self.O] * 2 + [self.F] * 3 + [P] * 4 + [self.O] * 3
        events, _ = run(GestureEngine(), seq)
        self.assertNotIn("press", kinds(events))

    def test_pinching_still_works_after_the_guard(self):
        engine = GestureEngine()
        _, t = run(engine, self.seq())
        events, _ = run(engine, [self.O] * int(1.5 / DT) + [frame(hand("pinch"))] * 5, t0=t)
        self.assertIn("press", kinds(events))

    def test_hud_shows_pending_second_fist(self):
        engine = GestureEngine()
        run(engine, [self.O] * 5 + [self.F] * 3 + [self.O] * 2)
        self.assertTrue(engine.hud_hands()[0]["fist_pending"])

    def test_controller_forgets_last_tap_and_changes_nothing(self):
        view = GraphView({"a": (0.0, 0.0), "b": (100.0, 50.0)}, 800, 600)
        view.fit()
        view.zoom_at(3.0, 400, 300)
        view.move_node_screen("a", 10, 10)
        state = (view.scale, view.ox, view.oy, dict(view.pos))
        ctrl = HandGraphController(view)
        ctrl._last_tap = (None, 1.0, 1.0, 0.0)
        ch = ctrl.apply([("unzoom", 0)], now=0.1)
        self.assertIsNone(ctrl._last_tap)
        self.assertFalse(ch["reset"])
        self.assertEqual((view.scale, view.ox, view.oy, view.pos), state)


class FistPan(unittest.TestCase):
    """Yumruk + el hareketi = grafiği kaydır, hiçbir düğüme dokunma (04.10 kullanıcı isteği: çok yakınlaşınca
    her sıkıştırma bir dosyaya denk geliyordu)."""

    O = frame(hand("open"))

    @staticmethod
    def fist_move(x0, x1, n):
        return [frame(hand("fist", cx=x0 + (x1 - x0) * k / max(1, n - 1))) for k in range(n)]

    def test_moving_fist_pans_without_press(self):
        seq = [self.O] * 5 + self.fist_move(0.5, 0.5, 4) + self.fist_move(0.5, 0.3, 15) + [self.O] * 4
        events, _ = run(GestureEngine(), seq)
        k = kinds(events)
        self.assertEqual(k.count("fist_pan_begin"), 1)
        self.assertGreater(k.count("fist_pan"), 5)
        self.assertEqual(k.count("fist_pan_end"), 1)
        self.assertNotIn("press", k)
        self.assertNotIn("unzoom", k)

    def test_still_fist_does_not_pan(self):
        events, _ = run(GestureEngine(), [self.O] * 5 + [frame(hand("fist"))] * 40 + [self.O] * 4)
        self.assertNotIn("fist_pan_begin", kinds(events))

    def test_double_fist_in_place_still_unzooms(self):
        F = frame(hand("fist"))
        events, _ = run(GestureEngine(), [self.O] * 5 + [F] * 3 + [self.O] * 3 + [F] * 3 + [self.O] * 3)
        self.assertEqual(kinds(events).count("unzoom"), 1)
        self.assertNotIn("fist_pan_begin", kinds(events))

    def test_clutching_fist_pan_is_not_a_double_fist(self):
        # Uzun kaydırma: yumrukla sürükle → aç → yeniden yumruk (geri gelip devam) yakınlaştırmadan çıkmamalı.
        seq = ([self.O] * 5 + self.fist_move(0.6, 0.3, 10) + [frame(hand("open", cx=0.45))] * 3
               + self.fist_move(0.6, 0.3, 10) + [self.O] * 3)
        events, _ = run(GestureEngine(), seq)
        self.assertNotIn("unzoom", kinds(events))
        self.assertEqual(kinds(events).count("fist_pan_begin"), 2)

    def test_fist_pan_disabled(self):
        seq = [self.O] * 5 + self.fist_move(0.5, 0.2, 20) + [self.O] * 4
        events, _ = run(GestureEngine(GestureParams(fist_pan=False)), seq)
        self.assertNotIn("fist_pan_begin", kinds(events))

    def test_fist_pan_moves_view_never_nodes_even_over_a_node(self):
        for mode3d in (False, True):
            view = GraphView({"a": (0.0, 0.0), "b": (300.0, 0.0)}, 1000, 750)
            view.radius = {"a": 8.0, "b": 8.0}
            view.fit()
            view.set_mode3d(mode3d)
            view.zoom_at(6.0, 500, 375)          # çok yakın: ekran düğümle dolu
            pos, angles = dict(view.pos), (view.yaw, view.pitch)
            ox, oy = view.ox, view.oy
            ctrl, engine = HandGraphController(view), GestureEngine()
            t = 0.0
            seq = [self.O] * 5 + self.fist_move(0.5, 0.5, 4) + self.fist_move(0.5, 0.25, 15) + [self.O] * 4
            moved, selected = set(), None
            for f in seq:
                ch = ctrl.apply(engine.update(f, t, 4 / 3)["events"], now=t)
                moved |= ch["moved"]
                selected = selected or ch["selected"]
                self.assertFalse(ch["rotate"])
                t += DT
            self.assertEqual(moved, set())
            self.assertIsNone(selected)
            self.assertEqual(view.pos, pos)
            self.assertEqual((view.yaw, view.pitch), angles)
            self.assertLess(view.ox, ox - 100, "el sola gidince grafik sola kaymalı")
            self.assertAlmostEqual(view.oy, oy, delta=30)
            self.assertEqual(ctrl.pan_last, {})

    def test_other_hand_pinch_ends_fist_pan_cleanly(self):
        engine = GestureEngine()
        run(engine, [frame(hand("open", cx=0.3), hand("open", cx=0.7))] * 5)
        seq = [frame(hand("fist", cx=0.3 - 0.01 * k), hand("open", cx=0.7)) for k in range(10)]
        seq += [frame(hand("fist", cx=0.2), hand("pinch", cx=0.7))] * 5
        events, _ = run(engine, seq, t0=1.0)
        k = kinds(events)
        self.assertIn("fist_pan_begin", k)
        self.assertIn("press", k)
        self.assertIn("fist_pan_end", k)
        self.assertNotIn("fist_pan", k[k.index("press"):], "tutuş başlayınca yumruk kaydırması sürmemeli")


class SafetyScope(unittest.TestCase):
    def test_hand_modules_never_touch_files_or_click(self):
        for mod in ("gestures.py", "hand_actions.py", "view.py"):
            src = (HERE.parent / "brain" / mod).read_text(encoding="utf-8")
            for forbidden in ("subprocess", "os.system", "open(", "pyautogui", "Quartz", "osascript"):
                self.assertNotIn(forbidden, src, f"{mod} içinde {forbidden}")

    def test_controller_emits_only_graph_changes(self):
        view = GraphView({"a": (0.0, 0.0)}, 400, 300)
        ch = HandGraphController(view).apply([("press", 0, 0.5, 0.5), ("drag", 0, 0.6, 0.5),
                                              ("release", 0, 0.6, 0.5, "open"), ("zoom", 1.2, 0.5, 0.5)])
        self.assertEqual(set(ch), {"moved", "pan", "zoom", "reset", "selected", "released", "rotate", "double"})


class View3D(unittest.TestCase):
    def _view(self):
        pos = {"a": (0.0, 0.0), "b": (300.0, 0.0), "c": (0.0, 200.0), "d": (-150.0, -80.0)}
        v = GraphView(pos, 1000, 750, depths={"a": 0.0, "b": 120.0, "c": -90.0, "d": 60.0})
        v.radius = {k: 8.0 for k in pos}
        v.set_mode3d(True)
        v.fit()
        return v

    def test_2d_mode_matches_plain_mapping(self):
        v = GraphView({"a": (10.0, 20.0)}, 800, 600, depths={"a": 500.0})
        v.scale, v.ox, v.oy = 2.0, 5.0, 7.0
        self.assertEqual(v.node_screen("a"), (25.0, 47.0))

    def test_move_node_roundtrip_keeps_depth(self):
        v = self._view()
        v.rotate(0.7, -0.4)
        depth = v.project("b")[3]
        v.move_node_screen("b", 420.0, 310.0)
        sx, sy = v.node_screen("b")
        self.assertAlmostEqual(sx, 420.0, places=6)
        self.assertAlmostEqual(sy, 310.0, places=6)
        self.assertAlmostEqual(v.project("b")[3], depth, places=6)

    def test_rotation_moves_nodes_but_not_positions(self):
        v = self._view()
        before = {k: v.node_screen(k) for k in v.pos}
        pos = dict(v.pos)
        self.assertTrue(v.rotate(0.5, 0.2))
        self.assertEqual(v.pos, pos)
        self.assertTrue(any(abs(v.node_screen(k)[0] - before[k][0]) > 1 for k in v.pos))
        self.assertFalse(v.rotate(float("nan"), 0.0))
        for _ in range(50):
            v.rotate(0.0, 0.3)
        self.assertLessEqual(abs(v.pitch), math.radians(85) + 1e-9)

    def test_hit_test_after_rotation(self):
        v = self._view()
        v.rotate(1.1, 0.3)
        for nid in v.pos:
            sx, sy = v.node_screen(nid)
            self.assertEqual(v.hit_test(sx, sy, slack_px=2), nid)

    def test_reset_restores_angles_and_positions(self):
        v = self._view()
        v.rotate(1.0, 0.5)
        v.move_node_screen("a", 10, 10)
        v.reset()
        self.assertEqual(v.pos, v.base)
        self.assertEqual(v.z, v.base_z)
        self.assertEqual(v.moved_nodes(), [])

    def test_hand_pinch_on_empty_rotates_in_3d(self):
        v = self._view()
        ctrl = HandGraphController(v)
        yaw, ox = v.yaw, v.ox
        ch = ctrl.apply([("press", 0, 0.02, 0.02), ("drag", 0, 0.12, 0.02)])
        self.assertTrue(ch["rotate"])
        self.assertNotEqual(v.yaw, yaw)
        self.assertEqual(v.ox, ox, "3B'de boşlukta sıkıştırma kaydırmaz, döndürür")
        ctrl.apply([("release", 0, 0.12, 0.02, "open")])
        self.assertFalse(ctrl.rot_last)


class DoublePinch(unittest.TestCase):
    def _setup(self):
        view = GraphView({"p": (0.0, 0.0), "q": (300.0, 0.0)}, 1000, 750)
        view.radius = {"p": 10.0, "q": 10.0}
        view.fit()
        ctrl = HandGraphController(view)
        px, py = view.node_screen("p")
        return view, ctrl, px / view.width, py / view.height

    def tap(self, ctrl, nx, ny, t, hold=0.2):
        ch1 = ctrl.apply([("press", 0, nx, ny)], now=t)
        ch2 = ctrl.apply([("release", 0, nx, ny, "open")], now=t + hold)
        return ch1, ch2

    def test_double_pinch_on_node_focuses_without_moving(self):
        view, ctrl, nx, ny = self._setup()
        ch, _ = self.tap(ctrl, nx, ny, 10.0)
        self.assertIsNone(ch["double"])
        ch = ctrl.apply([("press", 0, nx, ny)], now=10.5)
        self.assertEqual(ch["double"], "p")
        self.assertFalse(ctrl.grab, "ikinci sıkıştırma düğümü tutmaz")
        ch = ctrl.apply([("drag", 0, nx + 0.1, ny)], now=10.6)
        self.assertEqual(view.moved_nodes(), [])
        ctrl.apply([("release", 0, nx + 0.1, ny, "open")], now=10.7)
        self.assertFalse(ctrl._suppressed)

    def test_slow_or_moving_taps_are_not_double(self):
        view, ctrl, nx, ny = self._setup()
        self.tap(ctrl, nx, ny, 10.0)
        self.assertIsNone(ctrl.apply([("press", 0, nx, ny)], now=12.0)["double"], "çok geç")
        ctrl.apply([("release", 0, nx, ny, "open")], now=12.1)
        ctrl.apply([("press", 0, nx, ny)], now=20.0)
        ctrl.apply([("drag", 0, nx + 0.2, ny)], now=20.1)
        ctrl.apply([("release", 0, nx + 0.2, ny, "open")], now=20.2)   # sürükleme dokunma değildir
        self.assertIsNone(ctrl.apply([("press", 0, nx + 0.2, ny)], now=20.4)["double"])

    def test_double_pinch_on_empty_space_reports_empty(self):
        view, ctrl, _, _ = self._setup()
        self.tap(ctrl, 0.02, 0.95, 5.0)
        self.assertEqual(ctrl.apply([("press", 0, 0.02, 0.95)], now=5.4)["double"], "")


class ViewMath(unittest.TestCase):
    def test_zoom_keeps_anchor_point_fixed(self):
        v = GraphView({"a": (10.0, 20.0)}, 800, 600)
        before = v.to_world(300, 200)
        v.zoom_at(1.7, 300, 200)
        after = v.to_world(300, 200)
        self.assertAlmostEqual(before[0], after[0])
        self.assertAlmostEqual(before[1], after[1])

    def test_zoom_is_clamped(self):
        v = GraphView({"a": (0.0, 0.0)}, 800, 600)
        for _ in range(200):
            v.zoom_at(1.5, 0, 0)
        self.assertLessEqual(v.scale, 8.0)
        for _ in range(400):
            v.zoom_at(0.5, 0, 0)
        self.assertGreaterEqual(v.scale, 0.04)
        self.assertEqual(v.zoom_at(float("nan"), 0, 0), 1.0)

    def test_hit_test_finds_nearest_visible(self):
        v = GraphView({"a": (0.0, 0.0), "b": (40.0, 0.0), "c": (1000.0, 1000.0)}, 800, 600)
        v.radius = {"a": 6.0, "b": 6.0, "c": 6.0}
        v.scale, v.ox, v.oy = 1.0, 100.0, 100.0
        self.assertEqual(v.hit_test(102, 101), "a")
        self.assertEqual(v.hit_test(139, 100), "b")
        self.assertIsNone(v.hit_test(400, 400))
        v.set_visible({"b", "c"})
        self.assertNotEqual(v.hit_test(100, 100, slack_px=2), "a", "gizli düğüm tutulamaz")


if __name__ == "__main__":
    unittest.main()


class PinchRobustness(unittest.TestCase):
    """02.10.2026: kullanıcı "bazen kendiliğinden sıkıştırıyor, bazen sıkıştırdığımı anlamıyor" dedi."""

    @staticmethod
    def curled_pinch(factor=0.3):
        """Sıkı sıkıştırma: işaret parmağı ve başparmak ucu avuca doğru bükülü (işaret uzanımı ~1.0-1.1)."""
        lm = [list(p) for p in hand("pinch", pinch_gap=0.02)]
        base = lm[5]
        for i in (4, 6, 7, 8):
            lm[i] = [base[0] + (lm[i][0] - base[0]) * factor, base[1] + (lm[i][1] - base[1]) * factor, 0.0]
        return lm

    def test_tight_pinch_with_bent_index_is_recognised(self):
        lm = self.curled_pinch()
        m = hand_metrics(lm, 4 / 3)
        self.assertTrue(1.0 <= m["ext"][0] < 1.10, m["ext"])     # eski eşik (1.10) bunu reddediyordu
        engine = GestureEngine()
        events, _ = run(engine, [frame(hand("open"))] * 5 + [frame(lm)] * 6)
        self.assertIn("press", kinds(events))

    def test_fast_moving_hand_does_not_start_a_pinch(self):
        engine = GestureEngine()
        seq = [frame(hand("open", cx=0.2))] * 5
        # el ekranda hızla savrulurken parmaklar bir an kapanmış görünüyor (bulanık kareler)
        for k in range(14):
            seq.append(frame(hand("pinch", cx=0.2 + 0.045 * k)))
        events, t = run(engine, seq)
        self.assertNotIn("press", kinds(events))
        # el durunca aynı sıkıştırma kabul edilir
        events, _ = run(engine, [frame(hand("pinch", cx=0.8))] * 10, t0=t)
        self.assertIn("press", kinds(events))

    def test_brief_opening_during_drag_does_not_drop_the_grab(self):
        engine = GestureEngine()
        events, t = run(engine, [frame(hand("open"))] * 5 + [frame(hand("pinch"))] * 6)
        self.assertIn("press", kinds(events))
        seq = [frame(hand("halfpinch", pinch_gap=0.60))] * 2 + [frame(hand("pinch"))] * 4
        events, t = run(engine, seq, t0=t)
        self.assertNotIn("release", kinds(events))
        events, _ = run(engine, [frame(hand("open"))] * 8, t0=t)
        self.assertIn("release", kinds(events))

    def test_diagnostics_have_no_positions(self):
        engine = GestureEngine()
        run(engine, [frame(hand("open"))] * 5 + [frame(hand("pinch"))] * 6 + [frame(hand("open"))] * 8)
        lines = engine.pop_diag()
        self.assertTrue(any("sıkıştırma tutus" in ln for ln in lines), lines)
        self.assertTrue(any("sıkıştırma birakma" in ln for ln in lines), lines)
        self.assertEqual(engine.pop_diag(), [])
        for ln in lines:
            self.assertNotIn("cx", ln)
            self.assertNotIn("x=", ln)


class PinchAfterFist(unittest.TestCase):
    """03.10.2026 gerçek kayıt: yumruktan parmaklar kapalı hâlde doğrudan sıkıştırmaya geçince bekçi açık kalıyordu."""

    @staticmethod
    def pinch_others_curled():
        pinch, fist = hand("pinch"), hand("fist")
        return [list(p) for p in pinch[:9]] + [list(p) for p in fist[9:]]

    def test_pinch_with_curled_fingers_after_a_fist_is_accepted(self):
        lm = self.pinch_others_curled()
        m = hand_metrics(lm, 4 / 3)
        self.assertNotEqual(m["pose"], "fist")
        self.assertLess(sum(1 for e in m["ext"] if e > GestureParams().curled), 3)   # el tam açılmıyor
        engine = GestureEngine()
        seq = [frame(hand("open"))] * 5 + [frame(hand("fist"))] * 4
        seq += [frame(lm)] * int(1.6 / DT)          # yumruktan sonra el hiç açılmadan sıkıştırma
        events, _ = run(engine, seq)
        self.assertIn("press", kinds(events))
        self.assertNotIn("unzoom", kinds(events))

    def test_pinch_right_after_a_fist_is_still_blocked(self):
        engine = GestureEngine()
        seq = [frame(hand("open"))] * 5 + [frame(hand("fist"))] * 4 + [frame(self.pinch_others_curled())] * 6
        events, _ = run(engine, seq)
        self.assertNotIn("press", kinds(events))

    def test_real_fist_stays_rejected_with_lower_index_threshold(self):
        data = json.loads((HERE / "data" / "mediapipe_real_landmarks.json").read_text())["fist"]
        self.assertFalse(hand_metrics(data, 4 / 3)["pinch_ok"])
        events, _ = run(GestureEngine(), [[{"lm": data, "score": 1.0}]] * 30)
        self.assertNotIn("press", kinds(events))


class SecondHandAndSingleFist(unittest.TestCase):
    """04.10 kullanıcı isteği: ikinci el kolay görülsün; tek yumruk = seçimden çık (çift yumruk aynen kalır)."""

    O = frame(hand("open"))
    F = frame(hand("fist"))

    def test_second_hand_with_low_handedness_score_is_tracked(self):
        # Sağ/sol sınıflandırma güveni düşük (0,35) ikinci el önceden atılıyordu.
        f = [{"lm": hand("open", cx=0.3), "score": 0.95}, {"lm": hand("open", cx=0.72), "score": 0.35}]
        engine = GestureEngine()
        run(engine, [f] * 10)
        self.assertEqual(sum(1 for s in engine.slots if s.present), 2)
        self.assertEqual(engine.pop_counts()["valid2"], 10)

    def test_two_hand_zoom_with_low_score_hand(self):
        def two(gap):
            return [{"lm": hand("pinch", cx=0.5 - gap), "score": 0.95},
                    {"lm": hand("pinch", cx=0.5 + gap), "score": 0.4}]
        seq = [[{"lm": hand("open", cx=0.32), "score": 0.95}, {"lm": hand("open", cx=0.68), "score": 0.4}]] * 6
        seq += [two(0.18)] * 6 + [two(0.18 + 0.01 * k) for k in range(12)]
        events, _ = run(GestureEngine(), seq)
        self.assertIn("zoom", kinds(events))

    def test_ghost_duplicate_is_one_hand(self):
        lm = hand("open", cx=0.5)
        ghost = [[x + 0.004, y + 0.003, z] for x, y, z in lm]
        engine = GestureEngine()
        run(engine, [[{"lm": lm, "score": 0.9}, {"lm": ghost, "score": 0.6}]] * 10)
        self.assertEqual(sum(1 for s in engine.slots if s.present), 1)
        self.assertEqual(engine.pop_counts()["ghost"], 10)

    def test_single_fist_emits_fist_not_unzoom(self):
        events, _ = run(GestureEngine(), [self.O] * 5 + [self.F] * 4 + [self.O] * 30)
        self.assertEqual(kinds(events).count("fist"), 1)
        self.assertNotIn("unzoom", kinds(events))

    def test_double_fist_still_unzooms(self):
        seq = [self.O] * 5 + [self.F] * 3 + [self.O] * 3 + [self.F] * 3 + [self.O] * 3
        events, _ = run(GestureEngine(), seq)
        self.assertEqual(kinds(events).count("unzoom"), 1)
        self.assertEqual(kinds(events).count("fist"), 1)      # ilk yumruk seçimi kaldırır, ikincisi çıkar

    def test_controller_ignores_fist_event(self):
        view = GraphView({"a": (0.0, 0.0)}, 800, 600)
        ch = HandGraphController(view).apply([("fist", 0)], now=0.1)
        self.assertFalse(ch["reset"])
        self.assertIsNone(ch["selected"])


class FastClearPinch(unittest.TestCase):
    """04.10: parmaklar açıkça kapalıysa hareket hâlinde de tutulur (gerçek kayıtta 'hızlı' diye kaçan denemeler);
    eşik (0,27) ve yavaş eldeki davranış değişmedi."""

    def moving(self, step, gap, pinch_frames=8):
        seq, x = [], 0.2
        for _ in range(8):
            seq.append(frame(hand("open", cx=x)))
            x += step
        for _ in range(pinch_frames):
            seq.append(frame(hand("halfpinch", cx=x, pinch_gap=gap)))
            x += step
        return seq

    def test_clear_pinch_while_moving_is_grabbed(self):
        engine = GestureEngine()
        events, _ = run(engine, self.moving(0.017, 0.10))           # ~3,4 el boyu/sn, oran ~0,10
        self.assertIn("press", kinds(events))
        self.assertTrue(any("tutus:hızlı_net" in d for d in engine.pop_diag()))

    def test_borderline_pinch_while_moving_is_not_grabbed(self):
        engine = GestureEngine()
        events, _ = run(engine, self.moving(0.017, 0.25))           # oran ~0,25: eşik altı ama net değil
        self.assertNotIn("press", kinds(events))

    def test_very_fast_swing_is_not_grabbed(self):
        events, _ = run(GestureEngine(), self.moving(0.035, 0.05))  # ~7 el boyu/sn savrulma
        self.assertNotIn("press", kinds(events))

    def test_fast_path_needs_more_frames(self):
        events, _ = run(GestureEngine(), self.moving(0.017, 0.10, pinch_frames=3))
        self.assertNotIn("press", kinds(events))


class PinchDepthNoise(unittest.TestCase):
    """04.10: uçlardaki küçük derinlik (z) gürültüsü tutuşu kaçırmasın; açık derinlik ayrılığı yine tutuş değil."""

    def test_small_depth_noise_is_ignored(self):
        lm = hand("halfpinch", pinch_gap=0.20)
        base = hand_metrics(lm, 4 / 3)["pinch"]
        noisy = [list(p) for p in lm]
        span3 = hand_metrics(lm, 4 / 3)["span"]
        noisy[4][2] = noisy[8][2] + 0.2 * span3 / (4 / 3)   # el boyunun %20'si kadar z gürültüsü (x ölçeği)
        self.assertAlmostEqual(hand_metrics(noisy, 4 / 3)["pinch"], base, places=3)
        self.assertLess(hand_metrics(noisy, 4 / 3)["pinch"], GestureParams().pinch_in)

    def test_old_3d_measure_would_have_missed_it(self):
        lm = [list(p) for p in hand("halfpinch", pinch_gap=0.20)]
        span = hand_metrics(lm, 4 / 3)["span"]
        lm[4][2] = lm[8][2] + 0.22 * span / (4 / 3)
        d3 = math.sqrt(((lm[4][0] - lm[8][0]) * 4 / 3) ** 2 + (lm[4][1] - lm[8][1]) ** 2 + ((lm[4][2] - lm[8][2]) * 4 / 3) ** 2)
        self.assertGreater(d3 / span, GestureParams().pinch_in)          # eski ölçü: kaçar
        self.assertLess(hand_metrics(lm, 4 / 3)["pinch"], GestureParams().pinch_in)   # yeni: tutar
