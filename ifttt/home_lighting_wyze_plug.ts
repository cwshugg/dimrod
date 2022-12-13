// This is my Wyze plug home lighting IFTTT helper routine. It's structured like
// so:
//
//      STEP            SERVICE         DESCRIPTION
//      -------------------------------------------
//      IF              Webhooks        Receive a web request with a JSON payload
//      FILTER CODE     (this file)
//      THEN            Wyze            Turn plug on (plug 1)
//      THEN            Wyze            Turn plug off (plug 1)
//      THEN            Wyze            Turn plug on (plug 2)
//      THEN            Wyze            Turn plug off (plug 2)
//      THEN            Gmail           Send an email (to myself, for debugging)

// ============================ Helper Functions ============================ //
// Debug settings
const debug = false;
let debug_text = "<h1>Home Lighting Debug (Wyze Plug)</h1>";

// Takes a message and appends it to the global debug message string. It's
// appended to the string as a new line.
function debug_append(msg: string)
{
  debug_text += "<p>" + msg + "</p>";
  Gmail.sendAnEmail.setBody(debug_text);
}

// Simply disables sending the debug message.
function debug_skip()
{ Gmail.sendAnEmail.skip(); }


// =============================== Runner Code ============================== //
// first, parse the JSON data
let jdata = JSON.parse(MakerWebhooks.jsonEvent.JsonPayload);
const id = jdata["id"];
const action = jdata["action"].toLowerCase();

debug_append("Received payload: " + JSON.stringify(jdata));
debug_append("Plug ID: " + id);
debug_append("Plug Action: " + action);

// build a dictionary and list of all on-skip functions.
const on_skips: any = {
  "plug_front_porch1": [skip_on_fpp2],
  "plug_front_porch2": [skip_on_fpp1],
  "plug_front_porch_all": []
};

// build a dictionary and list of all off-skip functions.
const off_skips: any = {
  "plug_front_porch1": [skip_off_fpp2],
  "plug_front_porch2": [skip_off_fpp1],
  "plug_front_porch_all": []
};

// decide which skip functions to call based on the action
let skips: any = [];
if (action === "on")
{
  // if we're turning a plug on, we want to skip ALL 'off' actions,
  // as well as the 'on' actions that don't apply to our plug
  for (let key in off_skips)
  {
    const funcs = off_skips[key];
    for (let i = 0; i < funcs.length; i++)
    {
      skips.push(funcs[i]);
    }
  }
  const on_skip = on_skips[id];
  for (let i = 0; i < on_skip.length; i++)
  { skips.push(on_skip[i]); }
}
else
{
  // if we're turning a plug off, we want to skip ALL 'on' actions,
  // as well as the 'off' actions that don't apply to our plug
  for (let key in on_skips)
  {
    const funcs = on_skips[key];
    for (let i = 0; i < funcs.length; i++)
    {
      skips.push(funcs[i]);
    }
  }
  const off_skip = off_skips[id];
  for (let i = 0; i < off_skip.length; i++)
  { skips.push(off_skip[i]); }
}

if (debug)
{
  let skip_str = "Skip functions to invoke:";
  for (let i = 0; i < skips.length; i++)
  { skip_str += " <br> (" + typeof skips[i] + ") " + skips[i]; }
  debug_append(skip_str);
}

// now, invoke all skip methods to single out the one action we want
for (let i = 0; i < skips.length; i++)
{
  let sfunc: any = skips[i];
  sfunc();
}

// skip the debug step, if it's disabled
if (!debug)
{ debug_skip(); }

// =========================== Front Porch Plug 1 =========================== //
// Skips the turn-on action for this plug.
function skip_on_fpp1()
{
  debug_append("SKIPPING: fpp1.on");
  Wyzecam.plugTurnOn1.skip();
}

// Skips the turn-off action for this plug.
function skip_off_fpp1()
{
  debug_append("SKIPPING: fpp1.off");
  Wyzecam.plugTurnOff1.skip();
}

// =========================== Front Porch Plug 2 =========================== //
// Skips the turn-on action for this plug.
function skip_on_fpp2()
{
  debug_append("SKIPPING: fpp2.on");
  Wyzecam.plugTurnOn2.skip();
}

// Skips the turn-off action for this plug.
function skip_off_fpp2()
{
  debug_append("SKIPPING: fpp2.off");
  Wyzecam.plugTurnOff2.skip();
}

