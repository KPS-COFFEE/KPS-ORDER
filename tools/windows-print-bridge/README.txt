KPS Windows Print Bridge

1. Extract the ZIP into a permanent folder on the branch Windows computer.
2. Confirm the existing thermal printer prints a Windows test page.
3. In KPS Operations, open Branch printers, create a pairing code for this branch.
4. Double-click Start.cmd. It compiles the included source locally using Windows
   .NET Framework; it does not install a printer or change printer/network settings.
5. Select the EXISTING installed Windows printer by number. Enter the pairing code.
6. Keep the bridge window open and this Windows user signed in. The computer must
   remain awake and online. Start.cmd resumes the same registration after restart.

No inbound port, public printer IP, admin password or remote-desktop access required.
One bridge process per Windows account. The device key is encrypted with Windows
DPAPI for this user; never copy it to another computer. Pair each branch separately.

Queued: waiting for this computer.
Claimed: received by the bridge; physical output is not confirmed.
Submitted: accepted by the Windows printing API, NOT proof that paper emerged.
Uncertain: an error/crash may have occurred. Check Windows queue and physical paper
before manually requesting a REPRINT with a reason. Never clear a journal to retry.

The bridge never retries the physical print operation automatically. After a crash,
an interrupted local job is acknowledged as uncertain, not printed again. A server
job claimed before reaching the local journal remains claimed for manual review.
After restoring a database backup, revoke and pair printers again before use; review
pending jobs against the physical output and local journal.

Paper width is selected when pairing (58 or 80 mm). Configure the matching roll size
in the existing Windows driver. Long receipts paginate safely if the driver limits
custom paper height. Verify Arabic, logo, margins and a long order on real hardware
before enabling normal branch printing. No physical-printer test is claimed here.
To re-pair a revoked device: stop the running bridge, then run
Start.cmd --re-pair
Select the printer and enter a NEW pairing code. Old print journals are kept.
Restoring a server backup revokes printer devices and cancels pending prints;
pair again after the manager reviews the Windows print queue.

