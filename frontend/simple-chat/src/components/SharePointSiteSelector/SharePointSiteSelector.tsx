import { Dropdown, IDropdownOption } from "@fluentui/react";

import styles from "./SharePointSiteSelector.module.css";

interface Props {
    sites: string[];
    selectedSite?: string;
    onSiteChange: (site: string) => void;
    disabled?: boolean;
}

/**
 * Dropdown for choosing the SharePoint site that scopes the conversation.
 */
export const SharePointSiteSelector = ({ sites, selectedSite, onSiteChange, disabled }: Props) => {
    const options: IDropdownOption[] = sites.map(site => ({ key: site, text: site }));

    return (
        <div className={styles.container}>
            <Dropdown
                className={styles.dropdown}
                label="SharePoint site"
                placeholder="Select a SharePoint site to start chatting"
                options={options}
                selectedKey={selectedSite}
                disabled={disabled}
                onChange={(_ev, option) => {
                    if (option) {
                        onSiteChange(option.key as string);
                    }
                }}
            />
        </div>
    );
};
