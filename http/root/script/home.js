// Home page setup code.
//
//      Connor Shugg

// Document globals
const content = document.getElementById("content");
const tab_anchors = document.getElementById("tab_anchors");

// Services
const services = [
];

// Window-load function
window.onload = function()
{
    // set up page tabs
    const tab_overview = new Tab("overview", true);
    tab_overview.set_title("Overview");
    content.appendChild(tab_overview.html);
    tab_anchors.appendChild(tab_overview.anchor);

    // add an initial card to the overview tab
    const card_welcome = new Card("card_welcome");
    card_welcome.set_title("Welcome");
    card_welcome.set_text("This is my home server. It's under construction.");
    tab_overview.add_card(card_welcome);
}

