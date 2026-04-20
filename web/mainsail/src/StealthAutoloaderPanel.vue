<template>
    <!-- Panel only renders when the stealth_autoloader Klipper module is loaded -->
    <panel
        v-if="klipperReadyForGui && saExists"
        :title="$t('Panels.StealthAutoloaderPanel.Headline')"
        icon="mdi-pipe"
        :collapsible="true"
        card-class="stealth-autoloader-panel">

        <!-- Header action buttons (top-right of panel) -->
        <template #buttons>
            <v-btn icon tile @click="doSend('SA_HOME')" :title="$t('Panels.StealthAutoloaderPanel.Home')">
                <v-icon>mdi-home</v-icon>
            </v-btn>
            <v-btn icon tile @click="doSend('SA_ENGAGE')" :disabled="saServoEngaged" :title="$t('Panels.StealthAutoloaderPanel.Engage')">
                <v-icon>mdi-chevron-up</v-icon>
            </v-btn>
            <v-btn icon tile @click="doSend('SA_DISENGAGE')" :disabled="!saServoEngaged" :title="$t('Panels.StealthAutoloaderPanel.Disengage')">
                <v-icon>mdi-chevron-down</v-icon>
            </v-btn>
        </template>

        <v-card-text class="pa-0">
            <!-- Status summary row -->
            <v-row class="px-3 py-1 ma-0" dense>
                <v-col cols="auto" class="text-caption text--secondary">
                    Selector:
                    <strong>{{ saCurrentPath >= 0 ? 'T' + saCurrentPath : 'Unhomed' }}</strong>
                </v-col>
                <v-col cols="auto" class="text-caption ml-4">
                    Drive:
                    <strong :class="saServoEngaged ? 'orange--text' : 'grey--text'">
                        {{ saServoEngaged ? 'ENGAGED' : 'neutral' }}
                    </strong>
                </v-col>
                <v-spacer />
                <v-col cols="auto" class="text-caption text--secondary">
                    {{ loadedCount }} / {{ saNumPaths }} loaded
                </v-col>
            </v-row>

            <v-divider />

            <!-- Path table -->
            <v-simple-table dense>
                <template #default>
                    <thead>
                        <tr>
                            <th class="text-left">#</th>
                            <th class="text-left">State</th>
                            <th class="text-center" title="Entry sensor">EN</th>
                            <th class="text-center" title="Toolhead sensor">TH</th>
                            <th class="text-center" title="Extruder sensor">EX</th>
                            <th class="text-left">Material</th>
                            <th class="text-left">Color</th>
                            <th class="text-right"></th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr
                            v-for="path in saPaths"
                            :key="path.index"
                            :class="{ 'active-path': path.active }">

                            <!-- Path number -->
                            <td>
                                <strong :class="path.active ? 'primary--text' : ''">
                                    T{{ path.index }}
                                </strong>
                            </td>

                            <!-- State badge -->
                            <td>
                                <v-chip x-small :color="stateColor(path.state)" label>
                                    {{ stateLbl(path.state) }}
                                </v-chip>
                            </td>

                            <!-- Sensors -->
                            <td class="text-center">
                                <v-icon x-small :color="path.entry    ? 'success' : 'grey darken-1'">mdi-circle</v-icon>
                            </td>
                            <td class="text-center">
                                <v-icon x-small :color="path.toolhead ? 'success' : 'grey darken-1'">mdi-circle</v-icon>
                            </td>
                            <td class="text-center">
                                <v-icon x-small :color="path.extruder ? 'success' : 'grey darken-1'">mdi-circle</v-icon>
                            </td>

                            <!-- Material -->
                            <td class="text-caption">
                                <span v-if="path.material">
                                    {{ [path.brand, path.material].filter(Boolean).join(' · ') }}
                                </span>
                                <span v-else class="text--disabled">—</span>
                            </td>

                            <!-- Color -->
                            <td class="text-caption">
                                <span v-if="path.colorHex" class="d-flex align-center">
                                    <span
                                        class="color-dot mr-1"
                                        :style="{ background: path.colorHex }" />
                                    {{ path.colorName || path.colorHex }}
                                </span>
                                <span v-else class="text--disabled">—</span>
                            </td>

                            <!-- Load / Unload buttons -->
                            <td class="text-right">
                                <v-btn
                                    x-small color="success" class="mr-1"
                                    @click="doSend('SA_LOAD TOOL=' + path.index)">
                                    Load
                                </v-btn>
                                <v-btn
                                    x-small color="error"
                                    @click="doSend('SA_UNLOAD TOOL=' + path.index)">
                                    Unload
                                </v-btn>
                            </td>
                        </tr>
                    </tbody>
                </template>
            </v-simple-table>
        </v-card-text>
    </panel>
</template>

<script lang="ts">
import { Component, Mixins } from 'vue-property-decorator'
import BaseMixin from '@/components/mixins/base'
import StealthAutoloaderMixin from '@/components/mixins/stealthAutoloader'

@Component
export default class StealthAutoloaderPanel extends Mixins(BaseMixin, StealthAutoloaderMixin) {
    get loadedCount(): number {
        return this.saPathStates.filter((s) => s === 'loaded').length
    }

    stateColor(state: string): string {
        return (
            { loaded: 'success', empty: 'grey darken-2', partial: 'warning', unknown: 'amber darken-2' }[state] ??
            'grey'
        )
    }

    stateLbl(state: string): string {
        return { loaded: '● Loaded', empty: '○ Empty', partial: '≈ Partial', unknown: '? Unknown' }[state] ?? state
    }
}
</script>

<style scoped>
.active-path {
    background: rgba(33, 150, 243, 0.05);
}
.active-path td:first-child {
    border-left: 3px solid #2196f3;
    padding-left: 5px;
}
.color-dot {
    display: inline-block;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    border: 1px solid rgba(255, 255, 255, 0.2);
    flex-shrink: 0;
}
</style>
