# Lock Proof — Iteration 13B

## Test Method

Two PipelineCoordinator instances in same process:
1. First acquires lock (BLOCKING_EX | NB) — ACQUIRED
2. Second attempts acquire (BLOCKING_EX | NB) — DENIED
3. First releases lock
4. Third attempts acquire — ACQUIRED

## Results

```
FIRST_LOCK: ACQUIRED
Lock held by PID: 3468533
SECOND_LOCK: DENIED
FIRST_LOCK: RELEASED
THIRD_LOCK: ACQUIRED
THIRD_LOCK: RELEASED
LOCK_PROOF: PASSED
```

## Bug Fix Applied

The original `acquire_lock()` had a premature `fcntl.flock(fd, LOCK_UN)` after writing PID info, which released the lock before the caller could use it. This was fixed by removing the premature unlock:

**Before:**
```python
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
self._lock_fd = fd
fcntl.flock(fd, fcntl.LOCK_EX)  # redundant
os.ftruncate(fd, 0)
os.write(fd, lock_info.encode())
fcntl.flock(fd, fcntl.LOCK_UN)  # BUG: premature release
return True
```

**After:**
```python
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
self._lock_fd = fd
# Write PID info (lock held via fd — released only in release_lock)
os.ftruncate(fd, 0)
os.write(fd, lock_info.encode())
return True
```

Same fix applied to stale-lock recovery path.

## Lock Mechanism

- Type: fcntl.flock (advisory, kernel-level)
- File: state/.research_pipeline.lock
- Mode: LOCK_EX (exclusive)
- Stale detection: PID liveness check
- Recovery: automatic if owning PID is dead
