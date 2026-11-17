// Disclaimer.jsx: the always-visible notice at the bottom of the app.
// Project rule: the UI must show a "verify before travel" disclaimer for transport information.

export default function Disclaimer() {
  return (
    <div className="disclaimer">
      Trailwise is for planning and information only: no booking or payments. Transport details are indicative
      only: verify before travel.
    </div>
  );
}
