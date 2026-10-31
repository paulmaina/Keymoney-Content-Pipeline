Branding clips for Smart Village Boy
====================================

Put your own intro and outro clips in this folder with exactly these names. Each one you add is used in
place of the generated version; any that's missing uses the generated one. Remove a file to go back to
the generated version. The Remotion renderer uses them (the default); --no-branding leaves out every intro
and outro.

  File                            Size (W x H)   Suggested length   Used for
  intro_horizontal.mp4            1920 x 1080    2-4 s              the start of the full video
  intro_vertical.mp4              1080 x 1920    2-4 s              the start of every Short
  outro_horizontal.mp4            1920 x 1080    3-5 s              the end of the full video
  outro_vertical.mp4              1080 x 1920    3-5 s              the end of a single Short, and of the last part
  outro_next_part_vertical.mp4    1080 x 1920    3-5 s              optional: the end of every Short part that has
                                                                    another after it (instead of "Watch Part 2");
                                                                    a single clip, so it can't show the part number

Format
  - MP4 (H.264), 30 fps. Other sizes and frame rates work: the clip is resized and padded with black bars
    to fit the frame, never stretched or cropped, and converted to 30 fps. Clips under 1 second are ignored.
  - The last half second of an intro crossfades into the first scene, and the first half second of an
    outro crossfades out of the last scene, so keep text and logos clear of those moments.
  - In a Shorts series, the "PART 1 OF 4" badge is still drawn over your vertical intro, near the middle.
  - Keep the bottom fifth and the right edge of vertical clips free of anything important: YouTube's
    buttons and title cover them.

Audio
  - The clip's own sound is kept (a silent clip is fine).
  - With --music, the music fades in after your intro ends, so it never plays over the intro's sound,
    and it ducks under the outro's sound.
  - Keep the clip's sound at speaking level or a little lower: about -16 LUFS, peaks under -1 dB.
  - Only use music, voices and footage you have the right to use.

Templates
  The *_TEMPLATE.mp4 files are the generated intro and outro exported for you to edit (silent). Files
  ending in _TEMPLATE are never used in a render: edit one, then save it under the exact name above.
  Make them again (after changing the channel's name, tagline or colours) with:
    .venv\Scripts\python channels.py templates smart_village_boy

--check lists which intro and outro each video will use, and their lengths.
