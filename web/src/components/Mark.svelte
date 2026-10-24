<script lang="ts">
  import portrait from '../assets/clara.png';

  // Clara's mark: her portrait, a round picture with its own violet ring.
  let { size = 28 }: { size?: number } = $props();
</script>

<img class="mark" src={portrait} width={size} height={size} alt="" aria-hidden="true" decoding="async" />
