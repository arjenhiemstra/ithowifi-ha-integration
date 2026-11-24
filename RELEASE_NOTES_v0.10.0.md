## New feature

- **RF kWh meters.** The add-on can read Itho/Klimaatgarant RF kWh meters on request (firmware 3.4.0 or newer with an RF module). Every meter you configure on the add-on's **kWh meters** page shows up in Home Assistant as its own device, named as you named it there, with an energy sensor per counter (`total_increasing`, so it works in the Energy dashboard) and a last-reply time. Nothing to configure in Home Assistant: the integration picks the meters up from the add-on and adds a counter as soon as it carries a value.

With older firmware or without configured meters nothing changes.
