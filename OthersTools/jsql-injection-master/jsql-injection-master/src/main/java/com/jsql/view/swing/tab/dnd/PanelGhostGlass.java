
package com.jsql.view.swing.tab.dnd;

import java.awt.AlphaComposite;
import java.awt.Color;
import java.awt.Graphics;
import java.awt.Graphics2D;
import java.awt.Rectangle;

import javax.swing.JPanel;
import javax.swing.SwingUtilities;

@SuppressWarnings("serial")
public class PanelGhostGlass extends JPanel {
    
    private TabbedPaneDnD tabbedPane;
    
    public PanelGhostGlass(TabbedPaneDnD tabbedPane) {
        this.tabbedPane = tabbedPane;
        this.setOpaque(false);
    }
    
    public void setTargetTabbedPane(TabbedPaneDnD tab) {
        this.tabbedPane = tab;
    }
    
    @Override
    public void paintComponent(Graphics g) {
        Graphics2D g2 = (Graphics2D) g;
        Rectangle rect = this.tabbedPane.getDropLineRect();
        if(rect!=null) {
            Rectangle r = SwingUtilities.convertRectangle(this.tabbedPane, rect, this);
            g2.setComposite(AlphaComposite.getInstance(AlphaComposite.SRC_OVER, 0.5f));
            g2.setColor(new Color(34, 177, 76));
            g2.fill(r);
        }
    }
    
}
