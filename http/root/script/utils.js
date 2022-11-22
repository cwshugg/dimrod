// Various utility/helper functions.

// Globals
const btn_e_color = "mdl-color--grey-800 mdl-color-text--grey-200";
const btn_d_color = "mdl-color--grey-800 mdl-color-text--grey-600";


// ================================ Buttons ================================= //
// Initializes a butotn element to contain the correct MDL CSS fields.
function button_init(btn)
{
    btn.className = "mdl-button mdl-js-button mdl-button--raised mdl-js-ripple-effect";
    btn.className += " " + btn_e_color;
}

// Takes in a HTML button element (assumed to be material design lite) and marks
// it as disabled.
function button_disable(btn)
{
    btn.disabled = true;

    // add the appropriate CSS field
    if (!btn.className.includes("mdl-button--disabled"))
    { btn.className += " mdl-button--disabled"; }

    // change colors
    btn.className = btn.className.replace(btn_e_color, "");
    btn.className += btn_d_color;
}

// Takes in a HTML button element (assumed to be material design lite) and marks
// it as enabled.
function button_enable(btn)
{
    btn.disabled = false;

    // remove the CSS field
    btn.className = btn.className.replace("mdl-button--disabled", "");

    // change colors
    btn.className = btn.className.replace(btn_d_color, "");
    btn.className += btn_e_color;
}


// ================================ Helpers ================================= //
// Takes in a hex string and returns a JSON object in the following format:
//      {
//          "r": 255,
//          "b": 254,
//          "c": 253
//      }
function hex_to_rgb(hex)
{
    // remove any "#" characters from the string
    hex = hex.replace("#", "");

    // make sure the string is of the correct length. If it's not, return
    if (hex.length != 6)
    { return {"r": 255, "g": 255, "b": 255}; }
    
    // slice the string and parse as base-16 integers
    let r = hex.substring(0, 2);
    let g = hex.substring(2, 4);
    let b = hex.substring(4, 6);
    r = parseInt(r, 16);
    g = parseInt(g, 16);
    b = parseInt(b, 16);
    
    // build and return the JSON object
    return {"r": r, "g": g, "b": b};
}

// Takes in a RGB dictionary, as seen above, and converts it into a hex string
// without the preceding "#".
function rgb_to_hex(rgb)
{   
    // Internal helper function.
    function int_to_hex(i)
    {
        let str = Number(i).toString(16);
        return str.length == 1 ? "0" + str : str;
    }

    let result = int_to_hex(rgb.r) +
                 int_to_hex(rgb.g) +
                 int_to_hex(rgb.b);
    return result;
}

