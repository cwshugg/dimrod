// This is my Govee API home lighting IFTTT helper routine. It's structured like so:
//
//      STEP            SERVICE         DESCRIPTION
//      -------------------------------------------
//      IF              Webhooks        Receive a web request with a JSON payload
//      FILTER CODE     (this file)
//      THEN            Webhooks        Make a web request (for toggling power on/off)
//      THEN            Webhooks        Make a web reuqest (for setting light color)
//      THEN            Webhooks        Make a web reuqest (for setting light brightness)
//      THEN            Gmail           Send an email (to myself, for debugging)

// ============================ Helper Functions ============================ //
// Debug settings
const debug = false;
let debug_text = "<h1>Home Lighting Debug (Govee)</h1>";

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

// Skips all applet actions.
function skip_all()
{
  MakerWebhooks.makeWebRequest1.skip();
  MakerWebhooks.makeWebRequest2.skip();
  MakerWebhooks.makeWebRequest3.skip();
}

// ============================== Runner Code =============================== //
const govee_key = "GOVEE_API_KEY_GOES_HERE";
const govee_key_header = "Govee-API-Key: " + govee_key;

// parse the JSON data and set the on/off switch accordingly
let jdata = JSON.parse(MakerWebhooks.jsonEvent.JsonPayload);
debug_append("Received Payload: " + JSON.stringify(jdata));

// make sure the two required fields are present
if (jdata.hasOwnProperty("id") && jdata.hasOwnProperty("action"))
{
  const id = jdata["id"];
  const action = jdata["action"].toLowerCase();

  // create an ID-to-MAC-and-other-needed-data mapping
  const id_map: any = {
    "strip_staircase": {"mac": "MAC_ADDRESS_GOES_HERE", "model": "H6160"}
  }

  // make sure the ID matches one in our mapping
  if (id_map.hasOwnProperty(id))
  {
    const device_data = id_map[id];

    // REQUEST 1: update the light's status: on or off
    const req1_data = {
      "device": device_data["mac"],
      "model": device_data["model"],
      "cmd": {"name": "turn", "value": action}
    };
    MakerWebhooks.makeWebRequest1.setAdditionalHeaders(govee_key_header);
    MakerWebhooks.makeWebRequest1.setBody(JSON.stringify(req1_data));
    debug_append("Request 1 Payload: " + JSON.stringify(req1_data));

    // parse the color, if it was given
    let color = null;
    if (jdata.hasOwnProperty("color") && jdata["color"] !== null)
    {
      // build a JSON object suitable for the govee API
      color = jdata["color"];
      const cjson: any = {"r": color[0], "g": color[1], "b": color[2]};

      // create the request data, and set the API key
      const req2_data = {
        "device": device_data["mac"],
        "model": device_data["model"],
        "cmd": {"name": "color", "value": cjson}
      };
      MakerWebhooks.makeWebRequest2.setAdditionalHeaders(govee_key_header);
      MakerWebhooks.makeWebRequest2.setBody(JSON.stringify(req2_data));
      debug_append("Request 2 Payload: " + JSON.stringify(req2_data));
    }
    else
    { MakerWebhooks.makeWebRequest2.skip(); }

    // parse the brightness, if it was given
    let brightness = null;
    if (jdata.hasOwnProperty("brightness") && jdata["brightness"] !== null)
    {
      // convert the 0.0-1.0 float into an integer out of 100 (then
      // convert it to a string)
      brightness = jdata["brightness"];
      const bval = parseInt("" + (brightness * 100.0));
      
      // create the request data and set the API key
      const req3_data = {
        "device": device_data["mac"],
        "model": device_data["model"],
        "cmd": {"name": "brightness", "value": bval}
      };
      MakerWebhooks.makeWebRequest3.setAdditionalHeaders(govee_key_header);
      MakerWebhooks.makeWebRequest3.setBody(JSON.stringify(req3_data));
      debug_append("Request 3 Payload: " + JSON.stringify(req3_data));
    }
    else
    { MakerWebhooks.makeWebRequest3.skip(); }

  }
  else
  {
    debug_append("Unknown Govee device: \"" + id + "\".");
    skip_all();
  }
}
else
{
  debug_append("Payload is missing \"id\" or \"action\".");
  skip_all();
}

// if debug isn't enabled, skip it
if (!debug)
{ debug_skip(); }

