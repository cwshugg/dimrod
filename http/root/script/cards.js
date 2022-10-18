// JS code used to create material-design cards for the material-design-lite
// theme I'm borrowing from Google.
//
//      Connor Shugg

// Card globals
const card_section_class = "section--center mdl-grid mdl-grid--no-spacing mdl-shadow--2dp";
const card_div_class = "mdl-card mdl-cell--12-col";
const card_color_bg = "mdl-color--grey-900";
const card_color_text = "mdl-color-text--grey-300";

// Main card class.
class Card
{
    // Constructor. Takes in an ID for the HTML element.
    constructor(id)
    {
        this.id = id;

        // create the 'section' wrapper element
        this.html = document.createElement("section");
        this.html.className = card_section_class;
        this.html.id = this.id + "_section";

        // create the inner div (the actual card)
        this.div = document.createElement("div");
        this.div.className = card_div_class + " " +
                             card_color_bg + " " +
                             card_color_text;
        this.div.id = this.id + "_div";
        this.html.appendChild(this.div);

        // create a supporting text div
        this.stdiv = document.createElement("div");
        this.stdiv.className = "mdl-card__supporting-text";
        this.stdiv.id = this.id + "_stdiv";
        this.div.appendChild(this.stdiv);

        // set up a few internal fields for other parts of the card
        this.title = null;
        this.title_icon = null;
        this.actions = null;
    }

    // Takes in a title and updates the card's title text.
    set_title(title)
    {
        // if the title doesn't exist, create it
        if (!this.title)
        {
            this.title = document.createElement("h3");
            this.title.id = this.id + "_title";

            // ensure the title is at the top
            if (this.stdiv.children.length > 0)
            { this.stdiv.insertBefore(this.title, this.stdiv.children[0]); }
            else
            { this.stdiv.appendChild(this.title); }
        }
        this.title.innerHTML = title;
    }
    
    // Takes in a font-awesome class string and adds an icon to the card's
    // title.
    set_title_icon(fa_icon)
    {
        // make sure the title is initialized
        if (!this.title)
        { this.set_title(""); }

        // if the icon hasn't been set up, create it
        if (!this.title_icon)
        {
            const title_text = this.title.innerHTML;
            this.title_icon = document.createElement("i");
            this.title_icon.className = fa_icon;

            // reset the title's HTML and add the icon
            this.title.innerHTML = "";
            this.title.appendChild(this.title_icon);
            this.title.innerHTML += " " + title_text;
        }

        // update the icon's classname
        this.title_icon.className = fa_icon;
    }
    
    // Takes in an HTML element and sets the card's inner "supporting text"
    // HTML content
    add_html(html)
    {
        this.stdiv.appendChild(html);
    }
    
    // Takes in HTML and a function pointer and adds a button to the card's
    // action menu.
    add_action(id, html, func)
    {
        // create the actions 'div' if it doesn't exist yet
        if (!this.actions)
        {
            this.actions = document.createElement("div");
            this.actions.id = this.id + "_actions";
            this.actions.className = "mdl-card__actions mdl-card--border";
            this.div.appendChild(this.actions);
        }

        const button = document.createElement("button");
        button.id = id;
        button.className = "mdl-button mdl-js-button mdl-button--raised mdl-js-ripple-effect";
        button.className += " mdl-color--grey-800 mdl-color-text--grey-200";
        button.style.cssText = "margin: 8px;";
        button.addEventListener("click", func);

        // add the string (or HTML) to the button as necessary
        if (typeof html == "string")
        { button.innerHTML = html; }
        else
        { button.appendChild(html); }
        this.actions.appendChild(button);
    }
}

