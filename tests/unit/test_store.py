"""T6 - the store: sampler-owned memory, intents, retention, identity.

The checks the DoD names are all here: 100 interleaved intents from a second
thread while observe() iterates (0 lost, 0 exceptions), done retention then
expiry, a second kill yielding `already_exited`, `state_since` moving only on a
state change, slot adoption on restart, and a recycled pid not merging into the
old row.
"""

import threading
import unittest

from ostlr import config as config_mod
from ostlr.store import Store, StoreError, identity


def session(pid, started_at=1000, template="claude", cwd="D:/ws/projV",
            agent="claude", project="projV", mem=1024):
    return {
        "pid": pid,
        "ppid": 1,
        "started_at": started_at,
        "template_id": template,
        "agent": agent,
        "project": project,
        "cwd": cwd,
        "cmdline": "claude",
        "exe": "C:/bin/claude.exe",
        "mem_bytes": mem,
    }


def verdict(state, source="l2", reason="ok", age_s=1, kind="", msg="", exit_code=None):
    return {"state": state, "source": source, "reason": reason, "age_s": age_s,
            "kind": kind, "msg": msg, "exit_code": exit_code}


class TestIntents(unittest.TestCase):
    def test_unknown_kind_raises(self):
        store = Store()
        with self.assertRaises(StoreError):
            store.submit_intent("nonsense", pid=1)

    def test_missing_pid_raises(self):
        store = Store()
        with self.assertRaises(StoreError):
            store.submit_intent("mark_dirty")

    def test_bad_transient_raises(self):
        store = Store()
        with self.assertRaises(StoreError):
            store.submit_intent("transient", pid=1, transient="exploded")

    def test_queue_is_drained_once(self):
        store = Store()
        store.submit_intent("mark_dirty", pid=1, started_at=1000)
        self.assertEqual(store.pending_intents(), 1)
        self.assertEqual(store.apply_intents(), 1)
        self.assertEqual(store.pending_intents(), 0)
        self.assertEqual(store.apply_intents(), 0)

    def test_new_slot_creates_a_starting_row(self):
        store = Store()
        store.submit_intent("new_slot", pid=7, started_at=1000,
                            template_id="claude", cwd="D:/ws/x", now=5000)
        store.apply_intents()
        store.observe([], {}, now=5000)
        row = store.row_for(7, 1000)
        self.assertIsNotNone(row)
        self.assertEqual(row["transient"], "starting")
        self.assertEqual(row["template_id"], "claude")

    def test_starting_clears_on_first_real_state(self):
        store = Store()
        store.submit_intent("new_slot", pid=7, started_at=1000,
                            template_id="claude", cwd="D:/ws/x", now=5000)
        store.apply_intents()
        store.observe([session(7)], {7: verdict("working")}, now=5001)
        row = store.row_for(7, 1000)
        self.assertIsNone(row["transient"])
        self.assertEqual(row["state"], "working")

    def test_already_exited(self):
        store = Store()
        store.observe([session(9)], {9: verdict("done")}, now=1000)
        store.submit_intent("already_exited", pid=9, started_at=1000)
        store.apply_intents()
        store.observe([], {}, now=1001)
        self.assertTrue(store.is_already_exited(9, 1000))
        row = store.row_for(9, 1000)
        self.assertEqual(row["reason"], "already_exited")

    def test_already_exited_is_visible_immediately_and_survives_a_verdict(self):
        """The verifier's exact sequence (T6 return 1).

        The flag must be readable from `snapshot()` right after
        `apply_intents()`, and a later ladder verdict must not turn the
        incarnation back into `working` while its pid is still in the sample.
        """
        store = Store()
        store.observe([session(7, started_at=100)], {7: verdict("working")}, now=1000)
        store.submit_intent("already_exited", pid=7, started_at=100, now=1050)
        store.apply_intents()

        row = store.snapshot()[0]
        self.assertTrue(row["already_exited"])
        self.assertEqual(row["state"], "done")
        self.assertEqual(row["reason"], "already_exited")
        self.assertFalse(row["alive"])

        # The pid is still present in the next sample and the ladder still says
        # `working`: the exit wins.
        store.observe([session(7, started_at=100)], {7: verdict("working")}, now=1060)
        row = store.snapshot()[0]
        self.assertTrue(row["already_exited"])
        self.assertEqual(row["state"], "done")
        self.assertEqual(row["reason"], "already_exited")
        self.assertFalse(row["alive"])

    def test_already_exited_preserves_a_known_exit_code(self):
        store = Store()
        store.observe([session(7, started_at=100)],
                      {7: verdict("done", exit_code=3)}, now=1000)
        store.submit_intent("already_exited", pid=7, started_at=100, now=1050)
        store.apply_intents()
        store.observe([session(7, started_at=100)], {7: verdict("working")}, now=1060)
        row = store.snapshot()[0]
        self.assertEqual(row["exit_code"], 3)
        self.assertEqual(row["state"], "done")

    def test_already_exited_for_unknown_pid_still_records(self):
        store = Store()
        store.submit_intent("already_exited", pid=55, started_at=1)
        store.apply_intents()
        store.observe([], {}, now=10)
        self.assertTrue(store.is_already_exited(55, 1))

    def test_mark_dirty_does_not_decide_the_outcome(self):
        store = Store()
        store.observe([session(3)], {3: verdict("working")}, now=1000)
        store.submit_intent("mark_dirty", pid=3, started_at=1000)
        store.apply_intents()
        # The sampler still owns the verdict: the state is unchanged until a
        # fresh sample says otherwise.
        store.observe([session(3)], {3: verdict("working")}, now=1001)
        self.assertEqual(store.row_for(3, 1000)["state"], "working")


class TestConcurrency(unittest.TestCase):
    def test_100_interleaved_intents_while_observing(self):
        """The DoD check: 100 intents from a second thread, 0 lost, 0 raised."""
        store = Store()
        errors = []
        submitted = 100

        def producer():
            try:
                for i in range(submitted):
                    store.submit_intent("mark_dirty", pid=1000 + i, started_at=1)
            except Exception as exc:  # pragma: no cover - the failure we test for
                errors.append(exc)

        thread = threading.Thread(target=producer)
        thread.start()

        # Iterate hard while the producer runs: this is the exact hazard a
        # shared mutable dict would turn into RuntimeError or a dropped row.
        ticks = 0
        while thread.is_alive() or store.pending_intents():
            store.apply_intents()
            store.observe([session(1)], {1: verdict("working")}, now=1000 + ticks)
            store.snapshot()
            ticks += 1
            if ticks > 100000:  # pragma: no cover - a hang guard
                break

        thread.join()
        store.apply_intents()

        self.assertEqual(errors, [])
        self.assertEqual(store.applied_intents(), submitted)
        self.assertEqual(store.dropped_intents(), 0)
        self.assertEqual(store.pending_intents(), 0)

    def test_snapshot_is_a_copy(self):
        store = Store()
        store.observe([session(1)], {1: verdict("working")}, now=1000)
        snap = store.snapshot()
        snap[0]["state"] = "tampered"
        self.assertEqual(store.snapshot()[0]["state"], "working")


class TestRetention(unittest.TestCase):
    def test_done_row_is_retained_then_expires(self):
        cfg = config_mod.Config(done_retain_s=600)
        store = Store(cfg)
        store.observe([session(5)], {5: verdict("working")}, now=1000)
        # The process disappears -> done, retained.
        store.observe([], {}, now=1010)
        row = store.row_for(5, 1000)
        self.assertEqual(row["state"], "done")
        self.assertFalse(row["alive"])

        # Still inside the window.
        store.observe([], {}, now=1010 + 599)
        self.assertIsNotNone(store.row_for(5, 1000))

        # Past the window: gone.
        store.observe([], {}, now=1010 + 600)
        self.assertIsNone(store.row_for(5, 1000))

    def test_retention_is_configurable(self):
        cfg = config_mod.Config(done_retain_s=5)
        store = Store(cfg)
        store.observe([session(5)], {5: verdict("working")}, now=1000)
        store.observe([], {}, now=1010)
        store.observe([], {}, now=1015)
        self.assertIsNone(store.row_for(5, 1000))

    def test_live_row_never_expires(self):
        cfg = config_mod.Config(done_retain_s=1)
        store = Store(cfg)
        for t in range(0, 50, 5):
            store.observe([session(5)], {5: verdict("working")}, now=1000 + t)
        self.assertIsNotNone(store.row_for(5, 1000))


class TestStateSince(unittest.TestCase):
    def test_state_since_moves_only_on_a_change(self):
        store = Store()
        store.observe([session(1)], {1: verdict("working")}, now=1000)
        first = store.row_for(1, 1000)["state_since"]
        self.assertEqual(first, 1000)

        # Same state, later tick: the clock must NOT reset.
        store.observe([session(1)], {1: verdict("working")}, now=1005)
        self.assertEqual(store.row_for(1, 1000)["state_since"], 1000)

        # A real change moves it.
        store.observe([session(1)], {1: verdict("blocked", kind="permission")}, now=1010)
        self.assertEqual(store.row_for(1, 1000)["state_since"], 1010)

    def test_waiting_s_is_derived(self):
        store = Store()
        store.observe([session(1)], {1: verdict("blocked", kind="permission")}, now=1000)
        store.observe([session(1)], {1: verdict("blocked", kind="permission")}, now=1125)
        self.assertEqual(store.row_for(1, 1000)["waiting_s"], 125)


class TestIdentity(unittest.TestCase):
    def test_recycled_pid_does_not_merge(self):
        """A new process on a recycled pid is a NEW row, not the old one."""
        store = Store()
        store.observe([session(42, started_at=1000)],
                      {42: verdict("blocked", kind="permission")}, now=1000)
        self.assertEqual(store.row_for(42, 1000)["state"], "blocked")

        # Same pid, different creation time: a different incarnation.
        store.observe([session(42, started_at=2000)], {42: verdict("working")}, now=1001)
        new_row = store.row_for(42, 2000)
        self.assertIsNotNone(new_row)
        self.assertEqual(new_row["state"], "working")
        # The old incarnation is still remembered (it is dead, retained), but it
        # is a separate row and never inherited the new state. It reads `done`
        # because the process is gone - the point is that it is NOT `working`.
        old_row = store.row_for(42, 1000)
        self.assertIsNotNone(old_row)
        self.assertFalse(old_row["alive"])
        self.assertEqual(old_row["state"], "done")
        self.assertNotEqual(old_row["state"], new_row["state"])

    def test_identity_helper(self):
        self.assertEqual(identity(5, 10), (5, 10))
        self.assertEqual(identity("5", "10"), (5, 10))
        self.assertEqual(identity(None, None), (0, 0))

    def test_verdict_lookup_by_identity_wins(self):
        store = Store()
        store.observe([session(1, started_at=1000)],
                      {(1, 1000): verdict("error")}, now=1000)
        self.assertEqual(store.row_for(1, 1000)["state"], "error")


class TestSlots(unittest.TestCase):
    def test_restart_is_adopted_into_its_old_slot(self):
        store = Store()
        store.observe([session(10, started_at=1000, template="claude", cwd="D:/ws/p")],
                      {10: verdict("working")}, now=1000)
        self.assertEqual(store.slot_of("claude", "D:/ws/p"), (10, 1000))

        # The process dies and a restart appears with a new pid and a new
        # creation time, in the same directory with the same template.
        store.observe([], {}, now=1010)
        store.observe([session(11, started_at=2000, template="claude", cwd="D:/ws/p")],
                      {11: verdict("working")}, now=1011)
        self.assertEqual(store.slot_of("claude", "D:/ws/p"), (11, 2000))

    def test_slot_is_released_when_the_row_expires(self):
        cfg = config_mod.Config(done_retain_s=10)
        store = Store(cfg)
        store.observe([session(10, started_at=1000)], {10: verdict("working")}, now=1000)
        store.observe([], {}, now=1010)
        store.observe([], {}, now=1020)
        self.assertIsNone(store.slot_of("claude", "D:/ws/projV"))


class TestRobustness(unittest.TestCase):
    def test_empty_sample(self):
        store = Store()
        self.assertEqual(store.observe([], {}, now=1), [])

    def test_garbage_sessions_are_skipped(self):
        store = Store()
        store.observe([None, "x", {}, {"pid": 0}, {"pid": -3}], {}, now=1)
        self.assertEqual(store.row_count(), 0)

    def test_missing_verdict_is_unknown_never_idle(self):
        store = Store()
        store.observe([session(1)], {}, now=1000)
        row = store.row_for(1, 1000)
        self.assertEqual(row["state"], "unknown")
        self.assertEqual(row["source"], "none")

    def test_invalid_state_in_verdict_becomes_unknown(self):
        store = Store()
        store.observe([session(1)], {1: verdict("zombie")}, now=1000)
        self.assertEqual(store.row_for(1, 1000)["state"], "unknown")

    def test_reset(self):
        store = Store()
        store.observe([session(1)], {1: verdict("working")}, now=1000)
        store.reset()
        self.assertEqual(store.row_count(), 0)
        self.assertIsNone(store.row_for(1, 1000))


if __name__ == "__main__":
    unittest.main()
