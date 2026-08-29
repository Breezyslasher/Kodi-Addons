# Making InputStream Adaptive 21 work

On inputstream.adaptive **21.5.22** this addon's audio decodes to noise, and
on **22.3.20** the same media plays clean -- same account, same licence, same
bytes, and the same Widevine CDM (4.10.3050.0). So the difference is ISA's own
code.

## What is already ruled out

Measured on a real box, not argued:

* **Every AAC rendition.** 148, 149 and 150 offered one at a time, fragment
  sizes differing five-fold, all destroyed at the same instant.
* **The clear first fragment.** YouTube TV's audio arrives with fragment 1
  unencrypted and the rest encrypted; the ~9.5 seconds that used to play was
  that fragment. Hiding it (the `skip_clear_audio` setting) moved the failure
  from the tenth second to the first, so the clear-to-encrypted transition is
  not the trigger -- the encrypted audio is never decrypted at all.

Ruled out by reading 21.5.22 and 22.3.20 side by side:

* **Subsample handling.** 21's non-secure path -- the path this audio takes,
  per `GetCapabilities: Single decrypt possible` -- sets one subsample of 0
  clear and the whole sample encrypted when the media carries none. Correct.
* **The 8-byte IV.** Zero-padded to 16 identically in both.
* **`SetInput`.** Byte-identical between the versions.
* **The key.** 21 reads the track's own `tenc` default KID, 22 the DRM
  session's; both resolve to the audio track's key, and capabilities are
  probed per KID in both.
* **`[FragmentedSampleReader] Fix decryption switching` (037f999).** The
  obvious candidate by name, and `git merge-base` says it is not in 21.5.22 --
  but reading 21.5.22's `ReadSample` shows the fix is already there, backported
  under a different hash. Ancestry is misleading on a branch that backports.
* **Bento4.** The two forks differ only by AES-NI acceleration and
  AC-3/AV1/TrueHD sample-description fixes; the CENC parsing is unchanged.

## What this is

`0001-probe-the-cdm-call.patch` logs what ISA 21 actually hands the Widevine
CDM for each of the first 24 samples, and what comes back:

    ytv-probe: pool 1 in=1024 subs=1 [0/1024] scheme=1 pattern=0/0 kid=... iv=...
    ytv-probe: Decrypt -> status 0, out=1024

`build.sh` fetches Kodi Omega and ISA 21.5.22, applies it, and builds. Keep
both files together, or point at the patch directly:

    PATCH=~/Downloads/0001-probe-the-cdm-call.patch ./build.sh

**Build it on the machine that will run it.** A library built against one
distro or runtime will not load in another -- and if the Kodi 21 box is not
the one you build on, that includes it.

The patch has been checked to apply cleanly to a fresh 21.5.22 checkout, and
every API it uses was read out of that tree (`cdm::Pattern::crypt_byte_block`,
`LOG::Log(LOGINFO, ...)` as used elsewhere in the same file). It has **not**
been compiled -- there is no Kodi build environment where it was written.

## What to look for in the output

* an audio sample whose `kid` is not the audio track's own key id;
* a subsample layout that does not add up to `in=`;
* an unexpected `scheme` or `pattern` for CENC (expect scheme 1, pattern 0/0);
* `Decrypt -> status 0` with output that is really the input unchanged.

Any of those names the defect, and a real patch can be written against it.
