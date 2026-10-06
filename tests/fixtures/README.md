# tests/fixtures/

The decoder and the diff tool are tested against real traffic: three recordings
of a Fiat Doblò from [fmntf/fiatcan](https://github.com/fmntf/fiatcan)
(`Traces/`), each with fmntf's own decoding next to it.

fiatcan is published without a licence, so the recordings are not copied into
this repository. `fetch.sh` downloads the first 2000 lines of each from a fixed
commit of fiatcan:

```
tests/fixtures/fetch.sh
```

Without them the tests that need them are skipped; everything else runs.
