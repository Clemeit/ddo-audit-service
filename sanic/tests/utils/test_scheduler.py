import utils.scheduler as scheduler


class TestRunOnSchedule:
    def test_delegates_to_run_batch_on_schedule(self, monkeypatch):
        captured = {}
        sentinel = (lambda: None, lambda: None)

        def fake_run_batch_on_schedule(*args):
            captured["args"] = args
            return sentinel

        monkeypatch.setattr(
            scheduler, "run_batch_on_schedule", fake_run_batch_on_schedule
        )

        def sample_event():
            return None

        result = scheduler.run_on_schedule(sample_event, 5)

        assert captured["args"] == ((sample_event, 5),)
        assert result is sentinel


class TestRunBatchOnSchedule:
    def test_schedules_events_and_starts_stops_threads(self, monkeypatch):
        schedule_calls = []

        class FakeScheduleBuilder:
            def __init__(self, interval):
                self.interval = interval

            @property
            def seconds(self):
                return self

            def do(self, event):
                schedule_calls.append((self.interval, event))
                return self

        class FakeEvent:
            instances = []

            def __init__(self):
                self._set = False
                self.set_calls = 0
                FakeEvent.instances.append(self)

            def is_set(self):
                return self._set

            def set(self):
                self._set = True
                self.set_calls += 1

        class FakeThread:
            instances = []

            def __init__(self, target):
                self.target = target
                self.daemon = False
                self.name = ""
                self._alive = False
                self.start_calls = 0
                self.join_calls = 0
                FakeThread.instances.append(self)

            def is_alive(self):
                return self._alive

            def start(self):
                self._alive = True
                self.start_calls += 1

            def join(self):
                self._alive = False
                self.join_calls += 1

        printed = []

        monkeypatch.setattr(
            scheduler.schedule, "every", lambda interval: FakeScheduleBuilder(interval)
        )
        monkeypatch.setattr(scheduler.threading, "Event", FakeEvent)
        monkeypatch.setattr(scheduler.threading, "Thread", FakeThread)
        monkeypatch.setattr(
            "builtins.print", lambda *args, **kwargs: printed.append(args[0])
        )

        event_calls = []

        def event_a():
            event_calls.append("a")

        def event_b():
            event_calls.append("b")

        start, stop = scheduler.run_batch_on_schedule((event_a, 5), (event_b, 10))

        assert schedule_calls == [(5, event_a), (10, event_b)]
        assert len(FakeThread.instances) == 2
        assert FakeThread.instances[0].daemon is True
        assert FakeThread.instances[1].daemon is True
        assert FakeThread.instances[0].name == "event_a"
        assert FakeThread.instances[1].name == "event_b"

        FakeThread.instances[1]._alive = True

        start()

        assert event_calls == ["a"]
        assert FakeThread.instances[0].start_calls == 1
        assert FakeThread.instances[1].start_calls == 0
        assert "Started scheduler thread event_a" in printed

        stop()

        assert [item.set_calls for item in FakeEvent.instances] == [1, 1]
        assert [item.join_calls for item in FakeThread.instances] == [1, 1]
        assert "Stopped scheduler thread event_a" in printed
        assert "Stopped scheduler thread event_b" in printed
