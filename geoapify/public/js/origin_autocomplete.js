// geoapify_integration/public/js/origin_autocomplete.js

frappe.ui.form.on('Freight Quote', {
  refresh(frm) {
    const field = frm.fields_dict.origin;
    if (!field || !field.$input) return;

    const input = field.$input.get(0);
    if (input._geoapify) return;
    input._geoapify = true;

    const aw = new Awesomplete(input, { minChars: 3 });

    let results = [];

    input.addEventListener("input", frappe.utils.debounce(() => {
      frappe.call({
        method: "geoapify_integration.api.geoapify.autocomplete",
        args: { text: input.value },
        callback(r) {
          results = r.message || [];
          aw.list = results.map(x => x.label);
        }
      });
    }, 250));

    input.addEventListener("awesomplete-selectcomplete", e => {
      const match = results.find(x => x.label === e.text.value);
      if (match) {
        frm.set_value("origin_lat", match.lat);
        frm.set_value("origin_lon", match.lon);
      }
    });
  }
});

