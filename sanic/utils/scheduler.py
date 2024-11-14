import schedule
import threading
import time


def run_on_schedule(event: callable, interval: int) -> tuple[callable, callable]:
    """Run a single event on a schedule."""
    return run_batch_on_schedule((event, interval))


def run_batch_on_schedule(*args) -> tuple[callable, callable]:
    """Run a batch of events on a schedule."""
    scheduler_thread_pool: list[threading.Thread] = []

    for arg in args:
        event, interval = arg

        # Schedule the task to run every 5 seconds
        schedule.every(interval).seconds.do(event)

        # Create an event to control the scheduler thread
        stop_event = threading.Event()

        def run_schedule():
            while not stop_event.is_set():
                schedule.run_pending()
                time.sleep(1)

        scheduler_thread = threading.Thread(target=run_schedule)
        scheduler_thread.daemon = True
        scheduler_thread.name = event.__name__
        scheduler_thread_pool.append((scheduler_thread, event, stop_event))

    def start_schedule():
        """Start the scheduler thread."""
        for scheduler_thread, event, stop_event in scheduler_thread_pool:
            if not scheduler_thread.is_alive():
                event()
                scheduler_thread.start()
                print(f"Started scheduler thread {scheduler_thread.name}")

    def stop_schedule():
        """Stop the scheduler thread."""
        for scheduler_thread, event, stop_event in scheduler_thread_pool:
            stop_event.set()
            scheduler_thread.join()
            print(f"Stopped scheduler thread {scheduler_thread.name}")

    return start_schedule, stop_schedule
