# Design

Here we list a number of design considerations that were taken into account while implementing the `disk-objectstore`.

A detailed discussion of the design considerations are also summarized in the [AiiDA enhancement proposal AEP#006](https://github.com/aiidateam/AEP/blob/master/006_efficient_object_store_for_repository/readme.md) - you can refer to that page for additional details, describing the original motivations (from some requirements in the [AiiDA](https://www.aiida.net) code).

## Implementation considerations

This implementation, in particular, addresses the following aspects:

- objects are written, by default, in loose format. They are also uncompressed.
  This gives maximum performance when writing a file, and ensures that many writers
  can write at the same time without data corruption.

- the key of the object is its hash key. While support for multiple types of cryptographically
  strong hash keys is trivial, in the current version only `sha256` is used.
  The package assumes that there will never be hash collision.
  At a small cost for computing the hash (that is anyway small, see performance tests)
  one gets automatic de-duplication of objects written in the object store (git does something very
  similar).
  In addition, it automatically provides a way to check for corrupted data.

- loose objects are stored in a one-level sharding format: `aa/bbccddeeff00...`
  where `aabbccddeeff00...` is the hash key of the file.
  Current experience (with AiiDA) shows that it's actually not so good to use two
  levels of nesting.
  And anyway when there are too many loose objects, the idea
  is that we will pack them in few files (see below).
  The number of characters in the first part can be chosen, but a good compromise is
  2 (default, also used by git).

- for maximum performance, loose objects are simply written as they are,
  without compression.
  They are actually written first to a sandbox folder (in the same filesystem),
  and then moved in place with the correct key (being the hash key) only when the file is closed.
  This should prevent having leftover objects if the process dies, and
  the move operation should be hopefully a fast atomic operation on most filesystems.

- When the user wants, loose objects are repacked in a few pack files. Indeed,
  just the fact of storing too many files is quite expensive
  (e.g. storing 65536 empty files in the same folder took over 3 minutes to write
  and over 4 minutes to delete on a Mac SSD). This is the main reason for implementing
  this package, and not just storing each object as a file.

- packing can be triggered by the user periodically.

  **Note**: only one process can act on packs at a given time.

  **Note**: (one single) packer project can happen also while many other processes are
  writing *loose* objects and reading *any type* of object.
  To guarantee the possibility of concurrent operations, the loose objects are not removed
  while repacking.
  It is instead needed to run the `clean_storage()` method as discussed earlier,
  but this is a maintenance operation, so this can be run when no one is using
  the container in read or write mode.

  This packing operation takes all loose objects and puts them together in packs.
  Pack files are just concatenation of bytes of the packed objects. Any new object
  is appended to the pack (thanks to the efficiency of opening a file for appending).
  The information for the offset and length of each pack is kept in a single SQLite
  database.

- The name of the packs is a sequential integer. A new pack is generated when the
  pack size becomes larger than a per-container configurable target size.
  (`pack_size_target`, default: 4GB).
  This means that (except for the "last" pack), packs will always have a dimension
  larger or equal than this target size (typically around the target size, but
  it could be much larger if the last object that is added to the pack is very big).

- For each packed object, the SQLite database contains: the `uuid`, the `offset` (starting
  position of the bytestream in the file), the `length` (number of bytes to read),
  a boolean `compressed` flag, meaning if the bytestream has been zlib-compressed,
  and the `size` of the actual data (equal to `length` if `compressed` is false,
  otherwise the size of the uncompressed stream, useful for statistics), and an integer
  specifying in which pack it is stored. **Note** that the SQLite DB tracks only packed
  objects. Instead, loose objects are not tracked, and their sole presence in the
  loose folder marks their existence in the container.

- Note that compression is on a per-object level. This allows much greater flexibility
  (the API still does not allow for this, but this is very easy to implement).
  The current implementation only supports zlib compression with a default hardcoded
  level of 1 (good compression at affordable computational cost).
  Future extensions envision the possibility to choose the compression algorithm.

- the repository configuration is kept in a top-level json (number of nesting levels
  for loose objects, hashing algorithm, target pack size, ...)

- API exists both to get and write a single object, but also to write directly
  to pack files (this **cannot** be done by multiple processes at the same time, though),
  and to read in bulk a given number of objects.
  This is particularly convenient when using the object store for bulk import and
  export, and very fast. Also, it is useful when getting all files of a given node.

  In normal operation, however, we expect the client to write loose objects,
  to be repacked periodically (e.g. once a week).

- **PERFORMANCE**: Some reference results for bulk operations, performed on a
  Ubuntu 16.04 machine, 16 cores, 64GBs of RAM, with two SSD disks in RAID1 configuration),
  using the `examples/example_objectstore.py` script.

  - Storing 100'000 small objects (with random size between 0 and 1000 bytes, so a total size of around
    50 MB) directly to the packs takes about 21s.

  - The time to retrieve all of them is ~3.1s when using a single bulk call,
    compared to ~54s when using 100'000 independent calls (still probably acceptable).
    Moreover, even getting, in 10 bulk calls, 10 random chunks of the objects (eventually
    covering the whole set of 100'000 objects) is equally efficient as getting them
    all in one shot (note that for this size, only one pack file is created with the default
    configuration settings). This should demonstrate that exporting a subset of the graph should
    be efficient (and the object store format could be used also inside the export file).

    **Note**: these times are measured without flushing any disk cache.
    In any case, there is only a single pack file of about 50MB, so the additional time to
    fetch it back from disk is small. Anyway, for completeness, if we instead flush the caches
    after writing and before reading, so data needs to be read back from disk:

    - the time to retrieve 100000 packed objects in random order with a single bulk call is
      of about 3.8s, and in 10 bulk calls (by just doing this operation
      right after flushing the cache) is ~3.5s.
    - the time to retrieve 100000 packed objects in random order, one by one (right after
      flushing the cache, without doing other reads that would put the data in the cache already)
      is of about 56s.

- All operations internally (storing to a loose object, storing to a pack, reading
  from a loose object or from a pack, compression) are all happening via streaming.
  So, even when dealing with huge files, these never fill the RAM (e.g. when reading
  or writing a multi-GB file, the memory usage has been tested to be capped at ~150MB).
  Convenience methods are available, anyway, to get directly an object content, if
  the user wants.

- A number of streamins APIs are exposed to the users, who are encouraged to use this if they
  are not sure of the size of the objects and want to avoid out-of-memory crashes.

## Further design choices

In addition, the following design choices have been made:

- Each given object is tracked with its hash key.
  It's up to the caller to track this into a filename or a folder structure.
  To guarantee correctness, the hash is computed by the implementation
  and cannot be passed from the outside.

- Pack naming and strategy is not determined by the user, except for the specification
  of a `pack_size_target`. Pack are stored consecutively, so that when a pack file
  is "full", new ones will be used. In this way, once a pack it's full, it's not changed
  anymore (unless a full repack is performed), meaning that when performing backups using
  rsync, those full packs don't need to be checked every time.

- A single index file is used. Having one pack index per file turns out not
  to be very effective, mostly because for efficiency one would need to keep all
  indexes open (but then one quickly hits the maximum number of open files for a big repo with
  many pack files; this limit is small e.g. on Mac OS, where it is of the order of ~256).
  Otherwise, one would need to open the correct index at every request, that risks to
  be quite inefficient (not only to open, but also to load the DB, perform the query,
  return the results, and close again the file).

- Deletion (not implemented yet), can just occur as a deletion of the loose object or
  a removal from the index file. Later repacking of the packs can be used to recover
  the disk space still occupied in the pack files (care needs to be taken if concurrent
  processes are using the container, though).

- The current packing format is `rsync`-friendly. `rsync` has an algorithm to just
  send the new part of a file, when appending. Actually, `rsync` has a clever rolling
  algorithm that can also detect if the same block is in the file, even if at a
  different position. Therefore, even if a pack is "repacked" (e.g. reordering
  objects inside it, or removing deleted objects) does not prevent efficient
  rsync transfer.

  Some results: Let's considering a 1GB file that took ~4.5 mins to transfer fully
  the first time  over my network.
  After transferring this 1GB file, `rsync` only takes 14 seconds
  to check the difference and transfer the additional 10MB appended to the 1GB file
  (and it indeed transfers only ~10MB).

  In addition,  if the contents are randomly reshuffled, the second time the `rsync`
  process took only 14 seconds, transferring only ~32MB, with a speedup of ~30x
  (in this test, I divided the file in 1021 chunks of random size, uniformly
  distributed between 0 bytes and 2MB, so with a total size of ~1GB, and in the
  second `rsync` run I randomly reshuffled the chunks).

- Appending files to a single file does not prevent the Linux disk cache to work.
  To test this, I created a ~3GB file, composed of a ~1GB file (of which I know the MD5)
  and of a ~2GB file (of which I know the MD5).
  They are concatenated on a single file on disk.
  File sizes are not multiples of a power of 2 to avoid alignment with block size.

  After flushing the caches, if one reads only the second half, 2GB are added to the
  kernel memory cache.

  After re-flushing the caches, if one reads only the first half, only 1GB is added
  to the memory cache.
  Without further flushing the caches, if one reads also the first half,
  2 more GBs are added to the memory cache (totalling 3GB more).

  Therefore, caches are per blocks/pages in linux, not per file.
  Concatenating files does not impact performance on cache efficiency.

## Long-term support of the data
One of the goals of the `disk-objectstore` library is to adopt a relatively simple
packing mechanism, so that even if this library were to go unmaintained, it is always
possible to retrieve the data (the objects) with standard tools.

Here we discuss a simple bash script that allows to retrieve any object from a pack using
relatively standard tools (note: if it's loose, it's very simple: it's just a file in the
`loose` subfolder, with the filename equal to its hash, except for sharding).

```bash
#!/bin/bash
CONTAINER_PATH="$1"
HASHKEY="$2"

METADATA=`sqlite3 "$CONTAINER_PATH"/packs.idx 'SELECT offset, length, pack_id, compressed FROM db_object WHERE hashkey = "'"$HASHKEY"'"'`

if [ -z "$METADATA" ]
then
    echo "No object '" $HASHKEY "' found in container."
    exit 1
fi

IFS='|' read -ra METADATA_ARR <<< "$METADATA"
OFFSET=${METADATA_ARR[0]}
LENGTH=${METADATA_ARR[1]}
PACK_ID=${METADATA_ARR[2]}
COMPRESSED=${METADATA_ARR[3]}

let OFFSET_PLUS_ONE=OFFSET+1

if [ "$COMPRESSED" == "0" ]
then
    tail -c+$OFFSET_PLUS_ONE "${CONTAINER_PATH}/packs/${PACK_ID}" | head -c"${LENGTH}"
elif [ "$COMPRESSED" == "1" ]
then
    tail -c+${OFFSET_PLUS_ONE} "${CONTAINER_PATH}/packs/${PACK_ID}" | head -c"${LENGTH}" | zlib-flate -uncompress
else
    echo "Unknown compression mode "$COMPRESSED" for object '" $HASHKEY "'"
    exit 2
fi
```
This script gets two parameters. The first is the path to the container, and the second is the hashkey we want to
retrieve.

The requirements for this script to run are:

- a [bash shell](https://www.gnu.org/software/bash/), typically available (often by default) on Mac, Unix, and installable
  on Windows.
- the [sqlite3 executable](https://www.sqlite.org/index.html): this is typically easily installable in most operating
 systems and distributions. We also highlight that SQLite makes a strong commitment on long-term support for its
 format for decades, as documented in the [SQlite long-term support page](https://www.sqlite.org/lts.html).
- the `zlib-flate` executable: this comes from package `qpdf`, and it easily installable (e.g. on Ubuntu with
  the command `apt install qpdf`, or on Mac with [HomeBrew](https://brew.sh) using `brew install qpdf`).
  We note that this cmmand is actually needed only if the data is zlib-compressed (as a note, one cannot simply use
  the `gzip` command, as it also expects the gzip headers, that however are redundant and not used by the
  disk-objectstore implementation).

In addition, we highlight that both `zlib` and `sqlite3` are libraries that are part of the standard python libraries,
therefore one can very easily replace those calls with appropriately written short python scripts (e.g. calling
`zlib.decompressobj`).

## Performance
When this library was first implemented, many performance tests were run. These are collected in the folder
`performance-benchmarks` of the main [GitHub repository](https://github.com/aiidateam/disk-objectstore) of the
`disk-objectstore` package.

They are organized in appropriately named folders, with text files (README.txt or similar) discussing the results
of that specific performance test. Feel free to navigate that folder if you are curious of the tests that have been
performed.

In case you are curious you can also read
[issue #16 of the disk-objectstore repository](https://github.com/aiidateam/disk-objectstore/issues/16)
to get more details on the (significant) improvement in backup time when transferring (e.g. via rsync) the whole
container, with respect to just storing each object as a single file, when you have millions of objects.
The same issue also discusses the space saving (thanks to deduplication) for a real DB, as well as the cost of
keeping the index (SQLite DB).


## Concurrent usage of the disk-objectstore by multiple processes
The main goal of disk-objecstore is to allow to store objects efficiently, without a server running, with the
additional requirement to allow any number of concurrent **readers** (of any object, loose or packed: the reader
should not know) and **writers** (as long as they are OK to write to loose objects).
This allows to essentially use the library with any number of "clients".

In addition, a number of performance advantages are obtained only once objects are packed.
Therefore, another requirement is that some basic packing functionality can be performed while multiple clients are
reading and writing. Specific tests also stress-test that this is indeed the case, on the various platforms (Windows,
Mac and Unix) supported by the library.

However, packing **MUST ONLY BE PERFORMED BY ONE PROCESS AT A TIME** (i.e., it is invalid to call the packing methods
from more than one process at the same time).

Therefore, **the concurrent functionality that are supported include**:

- **any number of readers**, from any object
- **any number of writers**, to loose objects (no direct write to packs)
- **one single process to pack loose objects**, that can call the two methods `pack_all_loose()` and `clean_storage()`.

What is **NOT** allowed follows.
- one **MUST NOT** run two ore more packing number of operations at the same time.

In addition, a number of operations are considered **maintenance operations**. You should need to run them only
very rarely, to optimize performance. **Only one maintenance operation can run at a given time**, and in addition
**no other process can access (read or write) the container while a maintenance operation is running**.

This means that, before running a maintenance operation, **you should really stop any process using the container**,
run the maintenance operation, and then resume normal operation.

Maintenance operations include:
- deleting objects with `delete_objects()`
- adding objects directly to a pack with `add_streamed_objects_to_pack()` (or similar functions such as
  `add_objects_to_pack()`)
- repacking (to change compression, reclaim unused disk space in the pack files, ...) with `repack()` (and similarly
  for `repack_pack` to repack a single pack file).

A note: while one could implement guards (e.g. a decorator `@maintenance` for the relevant methods) to prevent
concurrent access (see e.g. [issue #6](https://github.com/aiidateam/disk-objectstore/issues/6)), this is complex to
implement (mostly because one needs to record any time a process starts accessing the repository - so a maintenance
operation can refuse to start - as well as check if any maintenance operation is running at every first access to a
container and refuse to start using it).
While not impossible, this is not easy (also to properly clean up if the computer reboots unexpectedly when a
maintenance operation is running, etc) and also might have performance implications as a check has to be performed
for every new operation on a Container.

We note that however such logic was [implemented in AiiDA](https://github.com/aiidateam/aiida-core/pull/5270)
(that uses `disk-objectstore` as a backend). Therefore guards are in place there, and if one needs to do the same
from a different code, inspiration can be taken from the implementation in AiiDA.
