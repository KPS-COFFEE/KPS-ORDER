# Compact cookie preview

Owner approved technical compression after the original 2,262,747-byte PNG failed to appear in a real WhatsApp composer (text preview appeared). Size is a suspected contributor, not a proven sole cause.

Original PNG retained unchanged, SHA256 815deea7ce4d56c203874f2d666086c776e2e6734290c4d7a472e333b263e1ae. New JPEG is 262,813 bytes, same 1536x1024 dimensions, no crop/regeneration, quality 84, baseline RGB JPEG. SHA256 2e1553c18c2e94174d7a8ce4e7f1d05ac0471d99ee2a1982aa4761c7906407a5.

39 focused tests pass on development and live-based versions. Live-only release a92c7db8bfc98ae1fade8f774975cc2c53381788 changes only HTML metadata and adds compact JPEG. Other streams remain undeployed. Actual WhatsApp acceptance still requires owner retest; automatic greeting untested.
