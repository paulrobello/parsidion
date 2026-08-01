User: the worker pool hangs after about a minute of load testing. goroutines pile
up and the whole thing seizes. can you look?

Agent: Reproduced locally with 200 concurrent submits. The deadlock shows up in
the pprof goroutine dump: every worker is blocked on mu.Lock() inside submit(),
while the dispatcher holds the mutex and is itself blocked sending to the
pending channel whose buffer is full. Classic cyclic wait.

User: why is the dispatcher holding the lock while it writes to the channel?

Agent: That is the bug. The mutex was meant to guard only the nextID counter,
but the lock scope got widened in a refactor to wrap the whole submit block,
including the channel send. Shrinking the critical section to just the counter
increment (defer mu.Unlock() right after nextID++) removes the cycle: the
dispatcher no longer holds the lock across the channel send, so workers can
drain pending and the deadlock clears.

User: good. how do we keep this from regressing?

Agent: Add a "go test -race" step that runs the pool under load for 30 seconds
in CI, and document the lock ordering on mu (acquire before pushing to pending,
never while blocked on a channel send). Filed as worker-pool/lock-ordering
with the pprof trace attached.

User: ship it.
