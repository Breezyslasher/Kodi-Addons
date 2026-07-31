# Draft report for InputStream Adaptive

Written up for <https://github.com/xbmc/inputstream.adaptive/issues>. Everything
below comes from Kodi debug logs and HAR captures of the same title played in a
browser; nothing is inferred. Attach a fresh debug log before posting, and check
whether a newer ISA release already changes the behaviour.

---

**Title:** HLS + Widevine with `cbcs` pattern encryption: video fails with
`kNoKey` while audio from the same licence decrypts

**Versions:** InputStream Adaptive 21.5.19 (Omega) · Kodi 21 (Flatpak,
`tv.kodi.Kodi`) · Linux x86_64 · software (L3) Widevine CDM

## Summary

Playing an HLS stream whose video track uses `cbcs` pattern encryption (1:9) and
whose audio track is encrypted without a pattern (0:0):

- audio decrypts and plays correctly;
- video fails ISA's capability test decryption, so the stream is flagged
  `SSD_SECURE_PATH` and decoded inside the CDM;
- `DecryptAndDecodeVideo` then returns `kNoKey` for a key id the licence
  demonstrably contains;
- immediately before that, ISA logs
  `ToCdmVideoCodecProfile: Unknown codec profile 0`, i.e. the CDM video decoder
  is initialised without a codec profile.

Both tracks are licensed through the same code path, and the licence server
returns exactly the key id that was requested in every case.

## Log extract

```
Opening stream: 4001 source: 256
Creating video codec with codec id: 27
AddOnLog: inputstream.adaptive: VideoCodec::Open
AddOnLog: inputstream.adaptive: ToCdmVideoCodecProfile: Unknown codec profile 0
Opening stream: 4005 source: 256
CDVDAudioCodecFFmpeg::Open() Successful opened audio decoder eac3
AddOnLog: inputstream.adaptive: DecryptAndDecodeVideo: Returned CDM status "kNoKey"
    for KID: 000000004ce10f056331202020202020
```

Audio (stream `4005`) plays. Video (stream `4001`) repeats `kNoKey` indefinitely.

## What was verified

| Check | Result |
|-------|--------|
| Key id requested vs. key id in the returned licence | Identical, every request, across several captures |
| Encryption scheme | video `tenc` pattern `1:9` (cbcs); audio `0:0` |
| Crypto mode | ISA maps `METHOD=SAMPLE-AES` → `AES_CBC` itself |
| Resolution | An SD variant fails identically to 1080p |
| Key id availability | The service sends no `KEYID` attribute and an all-zero `tenc` default_KID; supplying the real key id from the PSSH (both as `KEYID` and patched into `tenc`) does not change the failure |
| `NOSECUREDECODER` | Enabled; no effect, as it clears `SSD_SECURE_DECODER` rather than `SSD_SECURE_PATH` |

## Questions

1. Is `cbcs` pattern encryption (1:9) expected to work through the CDM decode
   path on desktop with the software CDM, or is only `cenc` supported there?
2. `ToCdmVideoCodecProfile: Unknown codec profile 0` — should a codec profile
   always be resolved before `InitializeVideoDecoder`, and can initialising the
   decoder without one cause the CDM to report `kNoKey`?
3. When a playlist's opening periods are unencrypted and `#EXT-X-KEY` first
   appears several periods in, ISA reports
   `GetStream(4001): Decrypter for the stream not found` and never sends a
   licence request. Is a decrypter expected to be created for a later period
   when the first period is clear?

## Reproducing

Any HLS stream that mixes clear opening periods with `cbcs` 1:9 pattern-encrypted
video and unpatterned audio should show it. The observations above come from a
third-party client for a commercial service, so a public sample stream would
need to be constructed to reproduce this without an account.
