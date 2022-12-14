// This is my main home lighting IFTTT helper routine. It's structured like so:
//
//      STEP            SERVICE         DESCRIPTION
//      -------------------------------------------
//      IF              Webhooks        Receive a web request with a JSON payload
//      FILTER CODE     (this file)
//      THEN            Webhooks        Make a web request (for LIFX API)
//      THEN            Webhooks        Make a web reuqest (for Wyze plugs)
//      THEN            Webhooks        Make a web reuqest (for Govee API)
//      THEN            Gmail           Send an email (to myself, for debugging)

// ========================== Globals and Toggles =========================== //
// Debug settings
const debug = true;
let debug_text = "<h1>Home Lighting Debug</h1>";

// API Keys
const lifx_key = "LIFX_API_KEY_GOES_HERE";

// Device-to-API mapping
const device_to_api: any = {
  "bulb_front_porch": lifx_handler,           // outdoor bulb above front door
  "plug_front_porch1": wyze_plug_handler,     // outdoor plug 1
  "plug_front_porch2": wyze_plug_handler,     // outdoor plug 2
  "plug_front_porch_all": wyze_plug_handler,  // both outdoor plug 1 and 2
  "strip_staircase": govee_strip_handler
}

// ============================== Runner Code =============================== //
// first, we need to parse all JSON fields from the HTTP request
let jdata = JSON.parse(MakerWebhooks.jsonEvent.JsonPayload);
const api_data: any = {};
api_data["id"] = jdata["id"];                       // (REQUIRED) ID/name string of the light
api_data["action"] = jdata["action"].toLowerCase(); // (REQUIRED) action to take

// parse the color as a trio of RBG values
//    "111,222,333"
api_data["color"] = null;                           // (OPTIONAL) color to set
if (jdata.hasOwnProperty("color"))
{
  // split the string by comma, then convert each string into a base-10 number
  const color_nums = jdata["color"].split(",");
  const color_arr = new Array();
  for (let i = 0; i < color_nums.length; i++)
  { color_arr.push(parseInt(color_nums[i], 10)); }
  api_data["color"] = color_arr;
}

// parse the brightness as a float (0.0 to 1.0)
api_data["brightness"] = null;                      // (OPTIONAL) brightness to set
if (jdata.hasOwnProperty("brightness"))
{
  // parse the brightness as a float, then ensure it's between 0.0 and 1.0
  let brightness = parseFloat(jdata["brightness"]);
  brightness = Math.min(1.0, brightness);
  brightness = Math.max(0.0, brightness);
  api_data["brightness"] = brightness;
}

// add a few debug lines
debug_append("<h2>Received Data</h2>");
debug_append("RAW: " + MakerWebhooks.jsonEvent.JsonPayload);
debug_append("ID: " + api_data["id"]);
debug_append("ACTION: " + api_data["action"]);
if (api_data["color"] !== null)
{
  debug_append("COLOR: " + 
               api_data["color"][0] + "-" +
               api_data["color"][1] + "-" +
               api_data["color"][2]);
}
else
{ debug_append("COLOR: null"); }
if (api_data["brightness"] !== null)
{ debug_append("BRIGHTNESS: " + api_data["brightness"]); }
else
{ debug_append("BRIGHTNESS: null"); }

// use the given ID string to find an API function to invoke
debug_append("<h2>API Mapping</h2>");
if (device_to_api.hasOwnProperty(api_data["id"]))
{
  const handler = device_to_api[api_data["id"]];

  // if the handler function isn't null, invoke it. Otherwise, we'll skip
  if (handler !== null)
  { handler(api_data); }
  else
  {
    debug_append("The device is in the mapping, but has a null handler.");
    skip_all();
  }
}
// if the ID string isn't in the mapping, skip all actions
else
{
  debug_append("The device isn't in the mapping.");
  skip_all();
}

// skip the debug step, if it's disabled
if (!debug)
{ debug_skip(); }

// ================================ Helpers ================================= //
// Invoked to skip ALL API calls relating to home lighting devices.
function skip_all()
{
  lifx_skip();
  wyze_plug_skip();
  govee_strip_skip();
}

// ============================= Debug Helpers ============================== //
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

// ================================== LIFX ================================== //
// The LIFX API documentation can be found here:
// https://api.developer.lifx.com/reference/introduction

// Main handler function, invoked when a device ID supplied is mapped to LIFX.
// The 'data' field is comprised of the parsed JSON values from the HTTP
// request. This function performs LIFX-specific actions to correctly ping the
// LIFX API.
function lifx_handler(data: any)
{
  debug_append("The device maps to LIFX.");

  // build a URL for the specific device that includes a valid 'selector' string
  // (which allows us to specify which light we want to interact with)
  const selector = "label:" + data["id"];
  const url = "https://api.lifx.com/v1/lights/" + selector + "/state";
  debug_append("URL: " + url);

  // set up the correct headers for the request (formatted as one long string of
  // HTTP headers, each separated by a \r\n)
  const headers = "Authorization: Bearer " + lifx_key + "\r\n";
                  "Content-Type: application/json";
  debug_append("HEADERS: " + headers);

  // set payload fields according to the action
  const payload: any = {}
  if (data["action"].localeCompare("on") == 0)
  { payload["power"] = "on"; }
  else if (data["action"].localeCompare("off") == 0)
  { payload["power"] = "off"; }
  else
  { payload["power"] = "off"; }

  // set the color, if applicable
  if (data["color"] !== null)
  {
    const cstr = "rgb:" +
                 data["color"][0] + "," +
                 data["color"][1] + "," +
                 data["color"][2];
    payload["color"] = cstr;
  }

  // set the brightness, if applicable
  if (data["brightness"] !== null)
  { payload["brightness"] = data["brightness"]; }

  // set all fields for the API request
  const payload_str = JSON.stringify(payload);
  debug_append("PAYLOAD: " + payload_str);
  MakerWebhooks.makeWebRequest1.setUrl(url);
  MakerWebhooks.makeWebRequest1.setMethod("PUT");
  MakerWebhooks.makeWebRequest1.setAdditionalHeaders(headers);
  MakerWebhooks.makeWebRequest1.setBody(payload_str);
}

// Helper function used to skip the LIFX API call.
function lifx_skip()
{ MakerWebhooks.makeWebRequest1.skip(); }


// ================================== Wyze ================================== //
// Handler function for Wyze plug devices.
function wyze_plug_handler(data: any)
{
  debug_append("The device maps to a Wyze plug.");

  // simply pass the data along to my other webhook we're going to ping
  MakerWebhooks.makeWebRequest2.setBody(JSON.stringify(data));
}

// Skips the Wyze plug actions.
function wyze_plug_skip()
{ MakerWebhooks.makeWebRequest2.skip(); }


// ================================= Govee ================================== //
// Handler function for Govee strip devices.
function govee_strip_handler(data: any)
{
  debug_append("The device maps to Govee.");

  // use the device-to-webhook mapping to set the correct URL
  const url = "https://maker.ifttt.com/trigger/home_lighting_govee/json/with/key/WEBHOOKS_KEY_GOES_HERE";
  MakerWebhooks.makeWebRequest3.setUrl(url);
  MakerWebhooks.makeWebRequest3.setBody(JSON.stringify(data));
  debug_append("Data sent to Govee: " + JSON.stringify(data));
}

// Skips the Govee strip actions.
function govee_strip_skip()
{ MakerWebhooks.makeWebRequest3.skip(); }

