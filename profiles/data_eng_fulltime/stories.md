# Gene sequence pipeline
The pipeline was initially failing with an out-of-memory error for large collections like plant. 

And because of this failure, the engineer increased the number of partitions for these tasks, This change had two effects:

1. First, it made the out of memory error go away.

2. Second, this was at the cost of the whole Python taking much longer, Because more partitions mean more overhead (initialization).

What I found was that the root cause for this error was not actually insufficient memory, but rather a misconfiguration of disk path (hardcoded so it wasn't split evenly across nodes) in the YARN cluster. This caused YARN to remove entire nodes from the cluster, eventually surfacing the error as an out-of-memory error.