KPS Windows Print Bridge

1. In KPS > Printer management, select the correct branch and Download branch setup.
2. On that branch's Windows computer, close the old bridge window, extract the
   downloaded ZIP into a permanent folder, then double-click Start.cmd.
3. The setup window shows the branch name. Select the installed thermal printer
   from its dropdown and click Connect. No console command or code copying needed.
   The setup package expires after 10 minutes and is single-use; download a fresh
   package if expired. It contains a secret: do not share it outside that branch.
4. Return to KPS. The status refreshes automatically. Click Print test page.
5. Check the physical paper, branch, logo, Arabic and margins, then click Approve.
6. Keep the bridge window open and this Windows user signed in. The computer must
   remain awake and online. The bridge registers automatic startup for this user.
   Keep the extracted folder in its permanent location.

The portal shows connection status and system notes. A missed heartbeat indicates
no connection; it does not prove sleep, power loss or an internet outage. Approval
is separate from live connectivity. A previously approved offline printer is not
shown as online. Historic print journals are preserved during replacement.

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


An owner, delivery manager or explicitly authorized printer administrator can create/revoke a pairing. Replacing a
registration or revoking it requires a recorded reason. Branch staff cannot open
printer administration. A selected remote print destination does not transfer
fulfillment ownership of the order. The customer branch is selected by default.

Before operational use, the manager sends Test print from Branch printers.
Verify the physical output at the correct branch (logo, Arabic, English, roll
width and margins), then confirm paper output in the portal. Windows submission
alone never approves a printer. Orders remain gated until this confirmation.
