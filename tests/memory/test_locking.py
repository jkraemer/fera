import threading
import time

from fera.memory.locking import file_lock


def test_lock_allows_sequential_access(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("original")

    with file_lock(str(f)):
        f.write_text("updated")

    assert f.read_text() == "updated"


def test_lock_blocks_concurrent_access(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("")
    order = []

    def writer(name, delay):
        with file_lock(str(f)):
            order.append(f"{name}-start")
            time.sleep(delay)
            order.append(f"{name}-end")

    t1 = threading.Thread(target=writer, args=("first", 0.2))
    t2 = threading.Thread(target=writer, args=("second", 0.0))
    t1.start()
    time.sleep(0.05)  # Ensure t1 gets the lock first
    t2.start()
    t1.join()
    t2.join()

    # first should complete before second starts
    assert order.index("first-end") < order.index("second-start")


def test_lock_releases_on_exception(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("")

    try:
        with file_lock(str(f)):
            raise ValueError("oops")
    except ValueError:
        pass

    # Should be able to acquire the lock again
    with file_lock(str(f)):
        pass  # No deadlock
