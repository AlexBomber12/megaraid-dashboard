# Operator action reference

These definitions explain the Actions panel. They do not authorize a hardware test.
Exact commands and gates remain in [the full rollout contract](STORCLI_FULL_ROLLOUT.md).

| Control | Effect |
| --- | --- |
| Start Maintenance | Opens a timed authorization window for supported controller writes. It does not start CC, Patrol Read or replacement. Hardware Telegram incidents remain eligible. |
| CC Start / Stop | Starts/stops RAID consistency checking: redundant data/parity are checked for agreement. This is not a filesystem check or backup. |
| CC Concurrent / Sequential | Checks virtual drives concurrently or one at a time. Hidden when the latest saved inventory contains exactly one VD; CC Start/Stop remain. With multiple VDs or unavailable inventory the switch remains subject to normal state/approval gates. |
| Patrol Read Start / Stop | Starts/stops background media reading to discover latent read errors. It can consume I/O bandwidth. |
| Patrol Auto / Manual | Firmware scheduling versus operator-initiated starts. Choosing Auto does not prove a pass just started or provide a next-run time. |
| Buzzer Silence | Silences the current audible condition while leaving alarm capability enabled. |
| Buzzer Enabled / Disabled | Enables/disables the controller's audible alarm. Enabled does not mean it is currently sounding; this setting does not control Telegram. |
| Foreign Review / Import / Clear | Reviews RAID metadata found on disks outside the active configuration; Import adopts it, Clear removes the foreign configuration. Neither is a routine health check. Review the exact configuration before confirmation. |
| Locate Start / Stop | Requests the selected drive's identification LED on/off. Controller acceptance does not prove the physical LED. Stop has a narrowly gated recovery exception outside maintenance. |
| Mark Good | Changes an eligible unconfigured bad disk to unconfigured good. It does not repair the physical drive or automatically return it to the RAID. |
| Spin Down | Stops an unconfigured HDD for removal; online array members are refused. |
| Assign Hot Spare | Assigns an eligible UGood disk as a dedicated reserve for the approved disk group, available for rebuild after a member failure. It does not add normal array capacity. |
| Replace Drive | Guided Offline → Missing → physical swap → Insert → rebuild monitoring, with serial/topology checks and typed confirmation. The physical swap remains an operator action. |

The selected mode buttons reflect the latest saved controller evidence, not an optimistic
click result. Unknown/disabled modes are not represented by a guessed selection.
Start/Stop availability also depends on the observed operation state and authorization
gates. A disabled button alone is not a current-state indicator. All physical effects
remain subject to the [readiness limits](OPERATOR_ACTION_READINESS.md).

Mark Bad is absent from the panel. Its retained API/wrapper refusal still rejects
unsupported requests for pinned StorCLI 1.23.02; removing the row grants no command.

Semantics references: [classic StorCLI manual](https://docs.broadcom.com/doc/12352476)
and [Broadcom software guide, CC scheduling](https://docs.broadcom.com/wcs-public/user-guide/products/design--reference-guides/user-guide/574/208/pub-005110_db15-001199-08_2017-06-09_mr-6.14-sw-ug.pdf).
These manuals do not replace exact-binary authorization or hardware evidence.
