# Notes: HLS + Widevine findings (resolved)

> **Resolved on Kodi 21 and 22.** The original `kNoKey` failure is fixed by
> recovering the key id from the PSSH (`KEYID` + `tenc` patching), a 360-pixel
> height cap and H.264-only variants. DRM is then configured to match the
> installed InputStream Adaptive: the JSON `inputstream.adaptive.drm` property
> (`secure_decoder`, `pre_init_data`) on **ISA 22.1.5+ (Kodi 22)**, and the
> legacy individual DRM properties (`license_type` / `license_key` /
> `server_certificate` / `pre_init_data`) on **ISA 21 (Kodi 21)**, which already
> force a single DRM session. The report below documents the original ISA 21
> `kNoKey` behaviour and is kept for reference; there is nothing to file.

> **Android (hardware Widevine L1) — HD/4K, Kodi 22 only.** On Android, ISA
> uses the device's own MediaCodec + system Widevine, which on a certified
> device is **L1**, so Apple's licence server grants the HD and 4K tiers
> (verified: 1080p H.264 and a ~3K HEVC tier both licence and render, with
> eac3 5.1 audio). Two things are needed there and are set only on Android:
> the **secure decoder** (`secure_decoder: true` in the JSON drm /
> `inputstream.adaptive.secure_decoder=true` legacy) — L1 decrypts into secure
> buffers that a non-secure MediaCodec cannot render (`ReleaseOutputBuffer
> error`, black picture) — and dropping the orphaned ac3/atmos audio renditions
> whose HEVC/DV variants were filtered (else ISA logs "Cannot find variant for
> AUDIO GROUP-ID" per rendition). The addon probes the level via
> `xbmcdrm.CryptoSession(...).GetPropertyString("securityLevel")` and lifts the
> default 360 cap to 1080 automatically on L1. A couple of transient
> `InstanceGuard locked` at bitrate switches remain but recover.
>
> **Why Kodi 21 on Android cannot work (ISA 21.5.x, verified in source and on
> device):** Apple keys audio separately from video, so playing needs one DRM
> session (licence) per key. ISA decides whether a new stream needs its own
> session by asking the decrypter `HasLicenseKey(session, keyId)`
> (`src/Session.cpp`, `InitializeDRM`). The **Android** decrypter in ISA 21
> hardcodes that answer:
>
> ```cpp
> // src/decrypters/widevineandroid/WVCencSingleSampleDecrypter.cpp:178 (21.5.22)
> bool CWVCencSingleSampleDecrypterA::HasLicenseKey(const std::vector<uint8_t>& keyId)
> {
>   // true = one session for all streams, false = one sessions per stream
>   return true;
> }
> ```
>
> so the video session claims to hold every key, the audio licence is never
> requested (device logs show exactly one licence request on Kodi 21 vs one per
> key on Kodi 22), and encrypted audio fails on every sample
> (`CDVDAudioCodecAndroidMediaCodec::Decode ExceptionCheck`) — the player then
> stalls/buffers indefinitely. Manifest-side workarounds don't exist: with
> `KEYID` omitted, ISA 21 re-extracts the key id from the PSSH
> (`HLSTree.cpp` "If there is no KID, try to get it from pssh data"). ISA 22
> replaced the stub with a real per-key check (`HasKeyId`, checks key status
> USABLE), which is why the same device on Kodi 22.0-BETA1 / ISA 22.3.19 plays
> HD with 5.1 audio. Desktop ISA 21 checks keys properly and is unaffected.

# Original draft report for InputStream Adaptive

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
