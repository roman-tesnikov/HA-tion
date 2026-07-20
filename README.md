# Tion breezers for Home Assistant

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.6%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io/)
[![Validate](https://github.com/roman-tesnikov/HA-tion/actions/workflows/validate.yml/badge.svg)](https://github.com/roman-tesnikov/HA-tion/actions/workflows/validate.yml)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/)
[![GitHub release](https://img.shields.io/github/v/release/roman-tesnikov/HA-tion)](https://github.com/roman-tesnikov/HA-tion/releases)
[![License](https://img.shields.io/github/license/roman-tesnikov/HA-tion)](LICENSE)

Custom Bluetooth integration for Tion S3, S4, and Lite breezers. This fork of
[TionAPI/HA-tion](https://github.com/TionAPI/HA-tion) is adapted for **Home
Assistant 2026.6 and newer** and includes fixes for Bluetooth connection
lifecycle and long-running CPU usage.

The integration allows Home Assistant to control:

- fan speed;
- target heater temperature;
- heater mode (on/off);
- Boost and Away presets.

If you control the breezer through MagicAir instead of Bluetooth, see
[tion_home_assistant](https://github.com/airens/tion_home_assistant).

> [!WARNING]
> A breezer is not a space heater. Do not use it to heat a room. You use this
> custom integration at your own risk.

## Compatibility

- Home Assistant Core `2026.6.0` and newer;
- a Home Assistant host with a working Bluetooth adapter or Bluetooth proxy;
- Tion S3, S4, or Lite breezer.

CI currently checks Home Assistant `2026.6.0` and `2026.7.2`. HACS installation
is recommended.

## Installation and configuration

### HACS installation

This fork is installed as a
[custom HACS repository](https://www.hacs.xyz/docs/faq/custom_repositories/):

1. Open **HACS → Integrations**.
2. Open the menu in the upper-right corner and select **Custom repositories**.
3. Add `https://github.com/roman-tesnikov/HA-tion` and select **Integration** as
   the category.
4. Find **Tion breezer** and download the latest release.
5. Restart Home Assistant.

### Configuration via the user interface

1. Open **Settings → Devices & services**.
2. Select **Add integration** and search for **Tion**.
3. Select **Tion breezer integration**, fill in the fields, and follow the
   instructions.
4. Repeat the setup for every breezer you want to use.

### Migrating from [TionAPI/HA-tion](https://github.com/TionAPI/HA-tion)

The upstream project and this fork use the same integration domain,
`ha_tion_btle`. Migration only replaces the integration files and the HACS
repository source; existing Home Assistant config entries and entities can be
preserved.

1. Create a Home Assistant backup.
2. In **HACS → Integrations**, open the installed **Tion breezer** entry from
   `TionAPI/HA-tion`, open its menu, and select **Remove**. This removes the
   downloaded integration files, not the related Home Assistant configuration
   data.
3. **Do not delete Tion breezer from Settings → Devices & services.** Deleting
   it there removes the existing config entry and is not part of the migration.
4. Open **HACS → Integrations → Custom repositories** and remove
   `TionAPI/HA-tion` from the custom repository list.
5. Add `https://github.com/roman-tesnikov/HA-tion` as a custom repository of
   type **Integration**.
6. Find **Tion breezer**, download the latest release from this fork, and
   restart Home Assistant.
7. Return to **Settings → Devices & services** and verify that the existing Tion
   devices and entities are available. No re-pairing or new config entry should
   be required.

If you need to roll back, repeat the same repository replacement in the other
direction or restore the backup made in step 1.

## Usage

### Turning on / Turning off

- Use the `climate.set_hvac_mode` action with `off`, `fan_only`, or `heat`.
  Changing the HVAC mode does not change the selected fan speed.
- Use the `climate.set_fan_mode` action with a fan mode from `1` to `6`. It
  turns the breezer on without changing the heater state.
- Use `climate.turn_on` and `climate.turn_off` to switch the breezer while
  preserving its previous operating state.

### Automation example

`automations.yaml`:

```yaml
- id: "tion_low_co2"
  alias: "Tion: speed 1 when CO2 is below 500 ppm"
  triggers:
    - trigger: numeric_state
      entity_id: sensor.mhz19_co2
      below: 500
      for: "00:05:00"
  conditions:
    - condition: not
      conditions:
        - condition: state
          entity_id: climate.tion_breezer
          state: "off"
  actions:
    - action: climate.set_fan_mode
      target:
        entity_id: climate.tion_breezer
      data:
        fan_mode: "1"

- id: "tion_high_co2"
  alias: "Tion: speed 4 when CO2 is above 600 ppm"
  triggers:
    - trigger: numeric_state
      entity_id: sensor.mhz19_co2
      above: 600
      for: "00:05:00"
  conditions:
    - condition: time
      after: "08:00:00"
      before: "22:00:00"
    - condition: not
      conditions:
        - condition: state
          entity_id: climate.tion_breezer
          state: "off"
  actions:
    - action: climate.set_fan_mode
      target:
        entity_id: climate.tion_breezer
      data:
        fan_mode: "4"
```

## Error reporting

Open issues in this fork's
[issue tracker](https://github.com/roman-tesnikov/HA-tion/issues). Include your
Home Assistant version, integration version, breezer model, and a debug log.
Enable debug logging in `configuration.yaml` with:

```yaml
logger:
  default: warning
  logs:
    custom_components.ha_tion_btle: debug
    custom_components.ha_tion_btle.lib.tion_btle.tion: debug
    custom_components.ha_tion_btle.lib.tion_btle.s3: debug
    custom_components.ha_tion_btle.lib.tion_btle.lite: debug
    custom_components.ha_tion_btle.lib.tion_btle.s4: debug
    custom_components.ha_tion_btle.config_flow: debug
```

## Bundled library

The integration contains a modified copy of
[tion-btle 3.3.6](https://github.com/TionAPI/tion_python) under the GNU Lesser
General Public License version 3. See `custom_components/ha_tion_btle/lib/tion_btle/LICENSE`
and `NOTICE`.
