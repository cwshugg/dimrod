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

