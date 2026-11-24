---
linkTitle: "Generate obstacles from a scan"
title: "Generate obstacles from a camera scan"
weight: 15
layout: "docs"
type: "docs"
description: "Scan the space around an arm with its wrist depth camera and write the result into the machine config as obstacles for motion planning."
capabilities: ["motion-planning"]
diataxis: how-to
date: "2026-11-24"
---

Instead of measuring and typing in every obstacle around an arm, let the arm map its own workspace.
The [`auto-obstacle-generation`](https://app.viam.com/module/viam/auto-obstacle-generation) module moves a wrist-mounted depth camera around the arm, fits boxes to everything it sees, and writes the boxes into your machine config as one obstacle component that every motion plan avoids.

## Prerequisites {#prerequisites}

- A machine running `viam-server` v1.12.0 or later and connected to Viam. The module writes the obstacles into the machine's config with the API key `viam-server` gives it, so it can't publish from an offline machine.
- An arm with a frame. See [Arm with gripper and wrist camera](/motion-planning/frame-system/arm-gripper-camera/).
- A depth camera that returns point clouds, mounted on the arm's wrist, with its frame parented to the arm or to a gripper on the arm. Intel RealSense and Orbbec cameras both work. See [Configure the camera frame](/motion-planning/frame-system/arm-gripper-camera/#5-add-a-frame-to-the-wrist-camera).
- A hand-eye-calibrated camera frame. An error of a few degrees in the camera's orientation puts every box in the wrong place. The `handeye` model in the [camera-calibration module](https://github.com/viam-labs/camera-calibration) computes the camera's pose on the arm.

## How the scan works {#how-the-scan-works}

The scanner moves the camera through rings of views around the arm's base and captures a point cloud at each one: 12 views per ring, and 5 rings by default, for 60 views.
It then moves the arm back to where it started, fuses the views into a 3D grid, and fits boxes around the parts of each object it saw: a table gets a box for its top and one for each leg.

The boxes go into the part config as one built-in obstacle component, `<scanner-name>-obstacles`, parented to `world`.
Each scan replaces that component.
Every plan the motion service makes avoids the boxes, whether it comes from the Viam app, an SDK, or another module.
Commands sent to the arm directly, such as joint moves from its card on the **CONTROL** tab, do not.

The scan plans its moves knowing only the obstacles already in your config, and it treats space the camera never saw as free.

## 1. Add the scanner service {#add-the-scanner-service}

On your machine's **CONFIGURE** tab, add the `auto-obstacle-generation` scanner service from the registry.
Set the arm that carries the camera and the camera itself:

```json
{
  "arm": "my-arm",
  "camera": "wrist-cam"
}
```

Save the config.
The scanner also depends on the machine's built-in motion service, which every machine has.

## 2. Fit the scan pattern to your arm {#fit-the-scan-pattern-to-your-arm}

The `rings` attribute sets where the camera goes: one ring of views per entry, as `[pitch_deg, radius_mm, height_mm]` in the arm's base frame.
The default rings suit a small arm such as a uFactory Lite6.
On a larger arm, the defaults can fold the arm back over its own base, so set rings the arm can reach that keep the camera beyond its minimum depth from the table.

For example, these rings suit a uFactory xArm6 with a gripper:

```json
{
  "arm": "my-arm",
  "camera": "wrist-cam",
  "rings": [
    [25, 350, 450],
    [0, 350, 450],
    [-25, 350, 450],
    [-50, 300, 500],
    [-85, 300, 600]
  ]
}
```

The scanner skips views the motion planner can't reach.
For every attribute and its default, see the [module README](https://github.com/viam-modules/auto-obstacle-generation#configuration).

## 3. Clear the space around the arm {#clear-the-space-around-the-arm}

The first scan moves the arm through the whole pattern without knowing what's around it.
Before you start:

- Move anything loose out of the arm's reach.
- Keep people out of the room. Anything that moves during the scan is mapped where it was.
- Move the arm to a pose clear of every object. The arm returns to this pose after the scan, and the scanner keeps boxes out of it.

## 4. Start the scan {#start-the-scan}

On the **CONTROL** tab, send this command from the scanner's **DoCommand** panel:

```json
{ "scan": {} }
```

The arm starts moving and the command returns at once with the scan's status.
A scan takes several minutes.
It runs in the background, so it continues if you close the tab.

To follow it, send `{ "status": {} }`.
The status `state` moves through `scanning`, `parking`, `fusing`, `fitting` and `publishing` to `done`, and `view` counts up to `total`.

To stop a scan, send `{ "cancel": {} }`.
The arm stops where it is, without moving back.

## 5. Verify the obstacles {#verify-the-obstacles}

1. Send `{ "status": {} }`.
   A finished scan has `"state": "done"`, and its `result` reports the number of boxes and `"published": "added"` (or `"replaced"` for a later scan).
   If `published` starts with `not published`, the boxes weren't written; see [Troubleshooting](#troubleshooting).
2. Open the **3D SCENE** tab.
   The `<scanner-name>-obstacles` component shows a box for each part of each object, colored to match the object when the camera returns color.
3. Compare the boxes to the real workspace:
   - Look for objects or faces the camera never saw, such as the back of a box or the far side of a shelf. The scan treats them as free space. Add them by hand in a separate obstacle component, because each scan replaces the scanner's own component. See [Configure workspace obstacles](/motion-planning/obstacles/configure-workspace-obstacles/).
   - Check that no box touches the arm in its resting pose. The planner ignores, for a whole move, any box the arm already touches when the move starts.

For the full verification workflow, see [Verify obstacles](/motion-planning/obstacles/verify-obstacles/).

## Manage scanned obstacles {#manage-scanned-obstacles}

### Rescan the workspace {#rescan-the-workspace}

When the workspace changes, send `{ "scan": {} }` again.
A later scan also avoids the boxes from the one before.
Its boxes replace the scanner's obstacle component only when the scan captured at least 80% of its views and fitted at least one box; otherwise the earlier boxes stay.

### Refit boxes without moving the arm {#refit-boxes-without-moving-the-arm}

After you update the module, or change `margin_mm` or reduce `workspace_radius_mm`, send `{ "refit": {} }`.
The scanner fits and publishes boxes again from its last saved scan, without moving the arm.
`refit` reuses the camera poses saved with that scan, so after you recalibrate the camera, scan again instead.
The status reports `"state": "done"` when the refit has published.

### Remove the obstacles {#remove-the-obstacles}

Send `{ "clear": {} }`.
The scanner removes its obstacle component from the config and returns `"result": "removed"`.

## Troubleshooting {#troubleshooting}

{{% expand "The scan is refused with \"not set: the scanner needs a machine connected to Viam\"" %}}

The scanner needs the machine part ID and API key that `viam-server` sets for a machine connected to Viam, to write its obstacles into the config.
It checks for them before the arm moves.
Run the machine connected to Viam, not from a local config file.

{{% /expand %}}

{{% expand "The status says \"cannot read the part config; it needs to be a machine owner key\"" %}}

The API key the module received can't edit the machine's config.
The scanner publishes with the key `viam-server` gives the module, which must have owner access to the machine.

{{% /expand %}}

{{% expand "The result says \"not published\" and lists views captured" %}}

The scan captured fewer than 80% of its planned views, usually because the planner couldn't reach them.
The status's `skipped` count says how many.
Adjust `rings` so the arm can reach more views, then scan again.

{{% /expand %}}

{{% expand "The boxes are offset or rotated from the real objects" %}}

The camera's frame doesn't match where the camera is on the arm.
Recalibrate the camera's frame, then send `{ "scan": {} }`.
`refit` won't fix this, because it reuses the camera poses saved with the last scan.

{{% /expand %}}

{{% expand "Two nearby objects share one box, or a box covers empty space" %}}

The scanner joins neighboring parts when one box around them is smaller than two separate boxes, and it can mistake noise for an object.
Edits you make to the scanner's boxes don't last, because each scan or refit replaces its component.
If an extra box blocks a path the arm needs, send `{ "clear": {} }` and configure that workspace by hand instead. See [Configure workspace obstacles](/motion-planning/obstacles/configure-workspace-obstacles/).

{{% /expand %}}

## What's next {#whats-next}

- [Verify obstacles](/motion-planning/obstacles/verify-obstacles/): check obstacle coverage against the physical workspace in the **3D SCENE** tab.
- [Configure workspace obstacles](/motion-planning/obstacles/configure-workspace-obstacles/): add obstacles the scan missed, in a component the scanner won't replace.
- [Plan collision-free paths](/motion-planning/obstacles/avoid-obstacles/): pass obstacles that change between moves at runtime.
- [auto-obstacle-generation README](https://github.com/viam-modules/auto-obstacle-generation): every attribute, command, and status field, and how the boxes are fitted.
