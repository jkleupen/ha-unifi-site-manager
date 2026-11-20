# UniFi Site Manager Monitoring

Home Assistant integration that monitors UniFi consoles and devices across **all** of your sites through the cloud [Site Manager API](https://developer.ui.com/site-manager-api), including firmware update availability.

It discovers consoles and devices automatically and creates entities for each. There is no pyscript, helper or secrets file to maintain: you paste an API key into the UI.

## What you get

**Per console (host)**
- `Connectivity` binary sensor
- `Site outage` problem sensor: on when several devices at that site have been down for a while
- `Devices offline` and `Firmware updates available` counts
- `Firmware` update entity for the console itself

**Per device (switch, AP, camera, access hub, ...)**, optional
- `Connectivity` binary sensor
- `Firmware` update entity (shows installed vs. available version)

**Account-wide**, on the "UniFi Site Manager" device
- `Status` sensor: `ok`, `devices_offline` or `console_offline` (handy for a red/amber/green badge)
- `Console offline`, `Device offline` and `Site outage` problem sensors
- Counts: consoles, consoles offline, devices, devices online, devices offline, unconfirmed offline, firmware updates available, ignored
- Attributes listing what is offline, grouped by host

The update entities are **read-only**: they report that a firmware update exists but never install anything.

## Install

1. In Home Assistant open the Marketplace (HACS) and choose **Add from a GitHub link**.
2. Paste `https://github.com/jkleupen/ha-unifi-site-manager` and download it.
3. Restart Home Assistant.
4. Go to **Settings > Devices & services > Add integration**, pick **UniFi Site Manager Monitoring** and enter your API key.

Create the key at [unifi.ui.com](https://unifi.ui.com) under **API > Create API Key**. The key is stored only in Home Assistant's config entry.

Manual install: copy `custom_components/unifi_site_manager` into your Home Assistant `config/custom_components` folder and restart.

## Options

Open the integration and choose **Configure**:

| Option | Default | Meaning |
|---|---|---|
| Polling interval | 15 min | How often the API is called |
| Ignore names starting with | `OLD,SPARE,IT Spare` | Case-insensitive prefixes. Matching consoles and devices are left out of all counts and get no entities. Devices on an ignored console are ignored too |
| Consecutive offline polls | 2 | A device or console counts as offline only after this many polls in a row, so a single blip does not alert |
| Devices down to raise a site outage | 2 | Number of devices at one site |
| Minutes those devices must be down | 30 | How long they must have been offline |
| Create per-device entities | on | Turn off to keep only consoles and the account-wide totals |

Changing an option reloads the integration.

## Notes and limits

- "Site" means a UniFi console (host) as the API reports it.
- "First seen offline" times and the offline counters are saved to Home Assistant's storage, so they survive restarts and reloads. The saved data is deleted if you remove the integration.
- Devices that disappear from your account leave their entities unavailable until you delete them in Home Assistant.
- With many devices the integration creates two entities per device. Turn off per-device entities if you only want the totals.
- The API enforces rate limits. The integration honors `Retry-After` and otherwise just tries again at the next poll.

## Development

The data logic lives in `processing.py` and has no Home Assistant imports. Run the tests with:

```
python tests/test_processing.py
```

## License

MIT
